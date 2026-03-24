"""
Unified virtual audio cable abstraction.

Detects and uses any compatible virtual cable (VB-Cable, VAC, etc.) for per-app
capture. Process loopback is preferred when available (no cable needed).

Note: Virtual cables require third-party drivers. This module cannot create
a cable—it detects existing installations. VB-Cable and VAC are common free options.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class VirtualCableDevice:
    """A detected virtual audio cable (input + output pair for routing)."""

    name: str
    input_device_id: str  # Apps output to this (virtual speaker)
    output_device_id: str  # We capture from this (loopback)
    provider: str = "unknown"


# Match device name to identify virtual cable pairs. Order: VB-Cable, VAC, generic.
VIRTUAL_CABLE_PATTERNS = [
    (r"CABLE Input|CABLE Output|VB-Audio Virtual Cable|VB-Cable", "vb_cable"),
    (r"Line \d+ \(Virtual Audio Cable\)|VAC|Virtual Audio Cable", "vac"),
    (r"Virtual.*Cable|Cable.*Virtual", "generic"),
]


def _is_cable_device(name: str) -> tuple[bool, str]:
    """Return (matches, provider) if name matches a virtual cable pattern."""
    for pattern, provider in VIRTUAL_CABLE_PATTERNS:
        if re.search(pattern, name, re.IGNORECASE):
            return True, provider
    return False, ""


def detect_virtual_cables() -> list[VirtualCableDevice]:
    """
    Detect installed virtual audio cables.

    Returns list of compatible cables (VB-Cable, VAC, etc.) with
    input/output device pairs for routing.
    """
    cables: list[VirtualCableDevice] = []
    try:
        import pyaudiowpatch as pyaudio

        pa = pyaudio.PyAudio()
        cable_inputs: list[tuple[int, dict, str]] = []
        cable_outputs: list[tuple[int, dict, str]] = []

        try:
            for i in range(pa.get_device_count()):
                dev = pa.get_device_info_by_index(i)
                name = dev.get("name", "")
                max_in = dev.get("maxInputChannels", 0)
                max_out = dev.get("maxOutputChannels", 0)
                matches, provider = _is_cable_device(name)
                if not matches:
                    continue

                # Playback device = apps send audio to (CABLE Input, Line 1 out)
                if (max_out > 0 and max_in == 0) or ("Input" in name and "Output" not in name):
                    cable_inputs.append((i, dev, provider))
                # Recording device = we capture from (CABLE Output, Line 1 in)
                elif max_in > 0 or "Output" in name:
                    cable_outputs.append((i, dev, provider))
        finally:
            pa.terminate()

        # Pair input (apps send) with output (we capture) of same provider
        used_outputs: set[int] = set()
        for iidx, idev, iprov in cable_inputs:
            for oidx, odev, oprov in cable_outputs:
                if oidx in used_outputs:
                    continue
                if iprov != oprov and iprov != "generic" and oprov != "generic":
                    continue
                prov = iprov if iprov != "generic" else oprov
                iname = idev.get("name", "")
                oname = odev.get("name", "")
                cables.append(
                    VirtualCableDevice(
                        name=iname if iname == oname else f"{iname} / {oname}",
                        input_device_id=str(iidx),
                        output_device_id=str(oidx),
                        provider=prov,
                    )
                )
                used_outputs.add(oidx)
                break

        seen_out: set[str] = set()
        unique: list[VirtualCableDevice] = []
        for c in cables:
            if c.output_device_id not in seen_out:
                seen_out.add(c.output_device_id)
                unique.append(c)

        logger.info("Detected virtual cables", count=len(unique))
        return unique

    except ImportError:
        return []
    except Exception as e:
        logger.exception("Failed to detect virtual cables", error=str(e))
        return []


def get_virtual_cable() -> VirtualCableDevice | None:
    """Get the first usable virtual cable. Prefers VB-Cable, then VAC."""
    cables = detect_virtual_cables()
    for p in ("vb_cable", "vac", "generic"):
        for c in cables:
            if c.provider == p:
                return c
    return cables[0] if cables else None


def is_virtual_cable_available() -> bool:
    """Check if any compatible virtual cable is installed."""
    return get_virtual_cable() is not None


def get_setup_url() -> str:
    """URL for free virtual cable (VB-Cable)."""
    return "https://vb-audio.com/Cable/"
