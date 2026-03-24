"""
Windows process loopback audio capture via ActivateAudioInterfaceAsync.

Captures audio from a specific process without VB-Cable. Requires Windows 10
build 20348+ (21H2 Server / Windows 11).
"""

from __future__ import annotations

import ctypes
import queue
import sys
import threading
import time
from ctypes import wintypes
from typing import Any, Callable, cast

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

# Windows build 20348 = 21H2 Server / Windows 11
PROCESS_LOOPBACK_MIN_BUILD = 20348

# VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK
VAD_PROCESS_LOOPBACK = "VAD\\Process_Loopback"

# PROCESS_LOOPBACK_MODE
PROCESS_LOOPBACK_MODE_INCLUDE = 0

# AUDIOCLIENT_ACTIVATION_TYPE
AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1

# WAVEFORMATEX
WAVE_FORMAT_PCM = 1
WAVE_FORMAT_IEEE_FLOAT = 3

# AUDCLNT
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000

# PROPVARIANT
VT_BLOB = 65

# UINT64 for ctypes (wintypes has c_ulonglong)
if hasattr(wintypes, "UINT64"):
    UINT64 = wintypes.UINT64  # type: ignore[attr-defined]
else:
    UINT64 = ctypes.c_ulonglong  # type: ignore[misc]


class GUID(ctypes.Structure):
    """GUID structure."""

    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]


class AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(ctypes.Structure):
    """AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS structure."""

    _fields_ = [
        ("TargetProcessId", wintypes.DWORD),
        ("ProcessLoopbackMode", wintypes.DWORD),
    ]


class AUDIOCLIENT_ACTIVATION_PARAMS(ctypes.Structure):
    """AUDIOCLIENT_ACTIVATION_PARAMS structure."""

    class _Params(ctypes.Union):
        _fields_ = [("ProcessLoopbackParams", AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS)]

    _fields_ = [
        ("ActivationType", wintypes.DWORD),
        ("_", _Params),
    ]


class PROPVARIANT_Blob(ctypes.Structure):
    """PROPVARIANT blob part."""

    _fields_ = [
        ("cbSize", wintypes.ULONG),
        ("pBlobData", ctypes.POINTER(wintypes.BYTE)),
    ]


class PROPVARIANT(ctypes.Structure):
    """Minimal PROPVARIANT for VT_BLOB."""

    _fields_ = [
        ("vt", wintypes.USHORT),
        ("wReserved1", wintypes.USHORT),
        ("wReserved2", wintypes.USHORT),
        ("wReserved3", wintypes.USHORT),
        ("blob", PROPVARIANT_Blob),
    ]


def is_process_loopback_supported() -> bool:
    """
    Check if Windows supports process loopback capture (build 20348+).

    Returns:
        True if supported, False otherwise.
    """
    if sys.platform != "win32":
        return False
    try:
        version = sys.getwindowsversion()
        build = version.build
        return build >= PROCESS_LOOPBACK_MIN_BUILD
    except Exception:
        return False


def _capture_loop(
    pid: int,
    sample_rate: int,
    _chunk_size_ms: int,
    audio_queue: queue.Queue[tuple[bytes, int]],
    is_capturing: threading.Event,
    on_error: Callable[[str], None] | None = None,
) -> None:
    """Main capture loop for process loopback (runs in thread)."""
    ole32 = None
    kernel32 = None
    sample_event = None
    audio_client = None
    capture_client = None
    stop_fn = None
    ac_vtbl = None
    cc_vtbl = None
    try:
        ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
        mmdevapi = ctypes.windll.mmdevapi  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        # CoInitializeEx STA - ActivateAudioInterfaceAsync for capture may require STA
        COINIT_APARTMENTTHREADED = 0x2
        ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)

        result_holder: dict[str, Any] = {}
        completion_event = threading.Event()

        def completion_handler_activate_completed(
            _this: ctypes.c_void_p, operation: ctypes.c_void_p
        ) -> int:
            try:
                vtbl = ctypes.cast(operation, ctypes.POINTER(ctypes.c_void_p))
                get_result_fn = ctypes.CFUNCTYPE(
                    ctypes.c_long,
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_long),
                    ctypes.POINTER(ctypes.c_void_p),
                )(ctypes.cast(vtbl[3], ctypes.c_void_p))  # type: ignore[call-overload]

                hr_result = ctypes.c_long()
                punk = ctypes.c_void_p()
                hr = get_result_fn(operation, ctypes.byref(hr_result), ctypes.byref(punk))
                if hr == 0 and hr_result.value == 0 and punk:
                    result_holder["audio_client"] = punk
                else:
                    result_holder["error"] = hr_result.value if hr == 0 else hr
            except Exception as e:
                result_holder["error"] = str(e)
            completion_event.set()
            return 0

        # Build activation params - keep alive for ActivateAudioInterfaceAsync
        loopback_params = AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(
            TargetProcessId=pid,
            ProcessLoopbackMode=PROCESS_LOOPBACK_MODE_INCLUDE,
        )
        activation_params = AUDIOCLIENT_ACTIVATION_PARAMS(
            ActivationType=AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK,
        )
        activation_params._.ProcessLoopbackParams = loopback_params

        # PROPVARIANT with blob pointing to activation_params
        activation_params_buffer = (wintypes.BYTE * ctypes.sizeof(activation_params))()
        ctypes.memmove(
            ctypes.byref(activation_params_buffer),
            ctypes.byref(activation_params),
            ctypes.sizeof(activation_params),
        )

        propvar = PROPVARIANT()
        propvar.vt = VT_BLOB
        propvar.blob.cbSize = ctypes.sizeof(activation_params)
        propvar.blob.pBlobData = ctypes.cast(
            ctypes.pointer(activation_params_buffer),
            ctypes.POINTER(wintypes.BYTE),
        )

        # IID_IAudioClient: 1CB9AD4C-DBFA-4c32-B178-C2F568A703B2
        iid_audio_client = GUID(
            Data1=0x1CB9AD4C,
            Data2=0xDBFA,
            Data3=0x4C32,
            Data4=(wintypes.BYTE * 8)(0xB1, 0x78, 0xC2, 0xF5, 0x68, 0xA7, 0x03, 0xB2),
        )

        # Completion handler vtable: QI, AddRef, Release, ActivateCompleted
        def qi(_s: ctypes.c_void_p, _riid: ctypes.c_void_p, _ppv: ctypes.c_void_p) -> int:
            return 0x80004002  # E_NOINTERFACE

        def addref(_s: ctypes.c_void_p) -> int:
            return 1

        def release(_s: ctypes.c_void_p) -> int:
            return 0

        act_fn = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(
            completion_handler_activate_completed
        )

        vtbl = (ctypes.c_void_p * 5)(
            ctypes.cast(ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(qi), ctypes.c_void_p),  # type: ignore[call-overload]
            ctypes.cast(ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(addref), ctypes.c_void_p),  # type: ignore[call-overload]
            ctypes.cast(ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(release), ctypes.c_void_p),  # type: ignore[call-overload]
            ctypes.cast(act_fn, ctypes.c_void_p),  # type: ignore[call-overload]
        )
        completion_obj = (ctypes.c_void_p * 2)(ctypes.addressof(vtbl), 0)

        ActivateAudioInterfaceAsync = mmdevapi.ActivateAudioInterfaceAsync  # type: ignore[attr-defined]
        ActivateAudioInterfaceAsync.argtypes = [
            ctypes.c_wchar_p,
            ctypes.POINTER(GUID),
            ctypes.POINTER(PROPVARIANT),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        ActivateAudioInterfaceAsync.restype = ctypes.c_long

        activation_op = ctypes.c_void_p()
        hr = ActivateAudioInterfaceAsync(
            VAD_PROCESS_LOOPBACK,
            ctypes.byref(iid_audio_client),
            ctypes.byref(propvar),
            ctypes.byref(completion_obj),
            ctypes.byref(activation_op),
        )

        if hr != 0:
            # 0x8000000E often indicates COM/threading or unsupported config
            hint = (
                " Try ensuring the target app is playing audio. "
                "If it persists, use 'All system audio' mode."
                if hr == 0x8000000E
                else ""
            )
            raise RuntimeError(f"ActivateAudioInterfaceAsync failed: 0x{hr:08X}.{hint}")

        completion_event.wait(timeout=10.0)
        if "error" in result_holder:
            err = result_holder["error"]
            raise RuntimeError(f"Activation callback failed: {err}")
        if "audio_client" not in result_holder:
            raise RuntimeError("Activation did not return IAudioClient")

        audio_client = result_holder["audio_client"]

        # Create stop_fn early so it can be used in finally for cleanup
        ac_vtbl = ctypes.cast(audio_client, ctypes.POINTER(ctypes.c_void_p))
        stop_fn = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(
            ctypes.cast(ac_vtbl[11], ctypes.c_void_p)  # type: ignore[call-overload]
        )

        class WAVEFORMATEX(ctypes.Structure):
            _fields_ = [
                ("wFormatTag", wintypes.WORD),
                ("nChannels", wintypes.WORD),
                ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD),
                ("nBlockAlign", wintypes.WORD),
                ("wBitsPerSample", wintypes.WORD),
                ("cbSize", wintypes.WORD),
            ]

        # Use fixed 16-bit PCM 44100 stereo (matches Microsoft sample). AUTOCONVERTPCM
        # lets the system convert from native format. GetMixFormat often fails for process
        # loopback (E_NOTIMPL) or returns formats that cause issues.
        wfx = WAVEFORMATEX(
            wFormatTag=WAVE_FORMAT_PCM,
            nChannels=2,
            nSamplesPerSec=44100,
            nAvgBytesPerSec=44100 * 2 * 2,
            nBlockAlign=4,
            wBitsPerSample=16,
            cbSize=0,
        )
        wfx_ptr = ctypes.pointer(wfx)
        init_flags = (
            AUDCLNT_STREAMFLAGS_LOOPBACK
            | AUDCLNT_STREAMFLAGS_EVENTCALLBACK
            | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
        )
        logger.info("Process loopback using 16-bit PCM 44100 stereo (AUTOCONVERT)")

        init_fn = ctypes.CFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(WAVEFORMATEX),
            ctypes.c_void_p,
        )(ctypes.cast(ac_vtbl[3], ctypes.c_void_p))  # type: ignore[call-overload]

        hr = init_fn(
            audio_client,
            AUDCLNT_SHAREMODE_SHARED,
            init_flags,
            0,
            0,
            wfx_ptr,
            None,
        )
        if hr != 0:
            raise RuntimeError(f"IAudioClient::Initialize failed: 0x{hr:08X}")

        get_buffer_size = ctypes.CFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(wintypes.UINT)
        )(ctypes.cast(ac_vtbl[4], ctypes.c_void_p))  # type: ignore[call-overload]
        buffer_frames = wintypes.UINT()
        hr = get_buffer_size(audio_client, ctypes.byref(buffer_frames))
        if hr != 0:
            raise RuntimeError(f"GetBufferSize failed: 0x{hr:08X}")

        # IID_IAudioCaptureClient: C8ADBD64-E71E-48a0-A4DE-185C395CD317
        iid_capture = GUID(
            Data1=0xC8ADBD64,
            Data2=0xE71E,
            Data3=0x48A0,
            Data4=(wintypes.BYTE * 8)(0xA4, 0xDE, 0x18, 0x5C, 0x39, 0x5C, 0xD3, 0x17),
        )

        get_service = ctypes.CFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )(ctypes.cast(ac_vtbl[14], ctypes.c_void_p))  # type: ignore[call-overload]
        capture_client = ctypes.c_void_p()
        hr = get_service(audio_client, ctypes.byref(iid_capture), ctypes.byref(capture_client))
        if hr != 0:
            raise RuntimeError(f"GetService(IAudioCaptureClient) failed: 0x{hr:08X}")

        sample_event = kernel32.CreateEventW(None, False, False, None)
        if not sample_event:
            raise RuntimeError("CreateEvent failed")

        set_event_handle = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HANDLE)(
            ctypes.cast(ac_vtbl[13], ctypes.c_void_p)  # type: ignore[call-overload]
        )
        hr = set_event_handle(audio_client, sample_event)
        if hr != 0:
            kernel32.CloseHandle(sample_event)
            raise RuntimeError(f"SetEventHandle failed: 0x{hr:08X}")

        start_fn = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(
            ctypes.cast(ac_vtbl[10], ctypes.c_void_p)  # type: ignore[call-overload]
        )
        hr = start_fn(audio_client)
        if hr != 0:
            kernel32.CloseHandle(sample_event)
            raise RuntimeError(f"IAudioClient::Start failed: 0x{hr:08X}")

        logger.info("Process loopback capture started", pid=pid, target_rate=sample_rate)

        # IAudioCaptureClient vtbl: 0-2 IUnknown, 3 GetBuffer, 4 ReleaseBuffer, 5 GetNextPacketSize
        cc_vtbl = ctypes.cast(capture_client, ctypes.POINTER(ctypes.c_void_p))
        get_buffer_fn = ctypes.CFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.POINTER(wintypes.BYTE)),
            ctypes.POINTER(wintypes.UINT),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(UINT64),
            ctypes.POINTER(UINT64),
        )(ctypes.cast(cc_vtbl[3], ctypes.c_void_p))  # type: ignore[call-overload]
        release_buffer_fn = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.UINT)(
            ctypes.cast(cc_vtbl[4], ctypes.c_void_p)  # type: ignore[call-overload]
        )
        get_next_packet_size = ctypes.CFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(wintypes.UINT)
        )(ctypes.cast(cc_vtbl[5], ctypes.c_void_p))  # type: ignore[call-overload]

        device_rate = 44100
        device_channels = 2
        bytes_per_frame = 4  # 16-bit stereo
        is_float = False

        plb_chunk_count = 0

        def resample(audio: np.ndarray, orig: int, target: int) -> np.ndarray:
            if orig == target:
                return audio
            try:
                from scipy import signal

                num = int(len(audio) * target / orig)
                return cast(np.ndarray, signal.resample(audio, num).astype(np.float32))
            except ImportError:
                ratio = target / orig
                indices = np.arange(0, len(audio), 1 / ratio)
                indices = np.clip(indices, 0, len(audio) - 1).astype(np.int32)
                return cast(np.ndarray, audio[indices].astype(np.float32))

        while is_capturing.is_set():
            wait_result = kernel32.WaitForSingleObject(sample_event, 100)
            if wait_result != 0:
                continue

            while True:
                frames_avail = wintypes.UINT()
                hr = get_next_packet_size(capture_client, ctypes.byref(frames_avail))
                if hr != 0 or frames_avail.value == 0:
                    break

                data_ptr = ctypes.POINTER(wintypes.BYTE)()
                flags = wintypes.DWORD()
                dev_pos = UINT64()
                qpc_pos = UINT64()
                hr = get_buffer_fn(
                    capture_client,
                    ctypes.byref(data_ptr),
                    ctypes.byref(frames_avail),
                    ctypes.byref(flags),
                    ctypes.byref(dev_pos),
                    ctypes.byref(qpc_pos),
                )
                if hr != 0:
                    break

                num_frames = frames_avail.value
                if num_frames > 0 and data_ptr:
                    num_bytes = num_frames * bytes_per_frame
                    buf = ctypes.string_at(data_ptr, num_bytes)
                    if is_float:
                        raw = np.frombuffer(buf, dtype=np.float32)
                    else:
                        raw = np.frombuffer(buf, dtype=np.int16)
                        raw = raw.astype(np.float32) / 32768.0
                    raw = raw.reshape(-1, device_channels).mean(axis=1)
                    raw = resample(raw, device_rate, sample_rate)
                    audio_bytes = raw.astype(np.float32).tobytes()
                    timestamp_ms = int(time.time() * 1000)
                    plb_chunk_count += 1
                    if plb_chunk_count <= 5 or plb_chunk_count % 200 == 0:
                        lvl = float(np.sqrt(np.mean(raw ** 2)))
                        logger.info(
                            "Process loopback chunk",
                            count=plb_chunk_count,
                            level=f"{lvl:.4f}",
                            samples=len(raw),
                        )
                    try:
                        audio_queue.put_nowait((audio_bytes, timestamp_ms))
                    except queue.Full:
                        try:
                            audio_queue.get_nowait()
                            audio_queue.put_nowait((audio_bytes, timestamp_ms))
                        except queue.Empty:
                            pass

                release_buffer_fn(capture_client, num_frames)

        logger.info(
            "Process loopback capture stopped",
            pid=pid,
            total_chunks=plb_chunk_count,
        )

    except Exception as e:
        logger.exception("Process loopback capture error", error=str(e))
        if on_error:
            try:
                on_error(str(e))
            except Exception:
                pass
    finally:
        # Cleanup resources in reverse order of creation; guard each to handle partial setup
        if kernel32 and sample_event:
            try:
                kernel32.CloseHandle(sample_event)
            except Exception:
                pass
        if stop_fn and audio_client:
            try:
                stop_fn(audio_client)
            except Exception:
                pass
        if cc_vtbl and capture_client:
            try:
                release_fn = ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(
                    ctypes.cast(cc_vtbl[2], ctypes.c_void_p)  # type: ignore[call-overload]
                )
                release_fn(capture_client)
            except Exception:
                pass
        if ac_vtbl and audio_client:
            try:
                release_fn = ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(
                    ctypes.cast(ac_vtbl[2], ctypes.c_void_p)  # type: ignore[call-overload]
                )
                release_fn(audio_client)
            except Exception:
                pass
        if ole32:
            ole32.CoUninitialize()


def run_process_loopback_capture(
    pid: int,
    sample_rate: int,
    chunk_size_ms: int,
    audio_queue: queue.Queue[tuple[bytes, int]],
    is_capturing: threading.Event,
    on_error: Callable[[str], None] | None = None,
) -> None:
    """
    Start process loopback capture in a daemon thread.

    Args:
        pid: Target process ID
        sample_rate: Output sample rate (e.g. 16000)
        chunk_size_ms: Chunk size in ms (used for queue timing)
        audio_queue: Queue to put (audio_bytes, timestamp_ms) tuples
        is_capturing: Event; clear to stop capture
    """
    thread = threading.Thread(
        target=_capture_loop,
        args=(pid, sample_rate, chunk_size_ms, audio_queue, is_capturing, on_error),
        daemon=True,
    )
    thread.start()
