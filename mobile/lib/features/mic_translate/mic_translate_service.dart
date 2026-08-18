import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:audio_session/audio_session.dart';
import 'package:dio/dio.dart';
import 'package:just_audio/just_audio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

import '../../services/api_client.dart';
import '../../services/auth_service.dart';
import 'playback_capture.dart';
import 'screen_ocr.dart';
import 'voice_profiles.dart';
import 'wav_pcm.dart';

/// Where Live Translate / Mic Translate pulls input from.
enum CaptureSource {
  /// Device microphone (Mic Translate).
  microphone,

  /// Other apps' playback via MediaProjection (Live → App audio).
  playback,

  /// On-screen text via MediaProjection frames + OCR (Live → Screen text).
  screen,
}

/// Captures mic or app playback in chunks, sends to backend, plays TTS.
class MicTranslateService {
  MicTranslateService({
    String targetLanguage = 'es',
    String sourceLanguage = 'auto',
    String voiceId = defaultVoiceId,
  })  : _targetLanguage = targetLanguage,
        _sourceLanguage = sourceLanguage,
        _voiceId = voiceId;

  /// Default ElevenLabs premade voice (Rachel).
  static const String defaultVoiceId = '21m00Tcm4TlvDq8ikWAM';

  String _targetLanguage;
  String _sourceLanguage;
  String _voiceId;

  String get targetLanguage => _targetLanguage;
  set targetLanguage(String value) => _targetLanguage = value;

  /// STT language hint (`auto` = detect).
  String get sourceLanguage => _sourceLanguage;
  set sourceLanguage(String value) => _sourceLanguage = value;

  /// ElevenLabs voice used for TTS / dubbing.
  String get voiceId => _voiceId;
  set voiceId(String value) {
    if (value.isNotEmpty) _voiceId = value;
  }

  final _api = ApiClient();
  final _auth = AuthService();
  final _record = AudioRecorder();
  // Duck (don't pause) other media like YouTube while TTS speaks.
  final _player = AudioPlayer(handleInterruptions: true);
  final _statusController = StreamController<String>.broadcast();
  final _paywallController = StreamController<void>.broadcast();
  final _sourceTextController = StreamController<String>.broadcast();
  final _translatedTextController = StreamController<String>.broadcast();
  final _voiceProfiles = VoiceProfileManager();
  bool _audioSessionReady = false;
  CaptureSource _captureSource = CaptureSource.microphone;
  StreamSubscription<Uint8List>? _playbackSub;
  ScreenOcr? _screenOcr;
  String _lastOcrNormalized = '';
  DateTime? _lastOcrEmittedAt;

  /// Speaker profiles (MFCC) → TTS voice mapping.
  VoiceProfileManager get voiceProfiles => _voiceProfiles;

  CaptureSource get captureSource => _captureSource;

  Stream<String> get statusStream => _statusController.stream;

  /// Latest transcribed source text after each successful STT chunk.
  Stream<String> get sourceTextStream => _sourceTextController.stream;

  /// Latest translated text after each successful translate chunk.
  Stream<String> get translatedTextStream => _translatedTextController.stream;

  /// Emits when API returns 402; show paywall.
  Stream<void> get paywallRequiredStream => _paywallController.stream;

  bool _running = false;
  bool _muted = false;
  bool _playbackActive = false;
  double _volume = 1.0;

  /// When true, capture/translate continue but TTS is skipped.
  bool get muted => _muted;

  set muted(bool value) {
    _muted = value;
    if (value) {
      unawaited(_player.stop());
    }
  }

  /// TTS output volume 0.0–1.0.
  double get volume => _volume;

  set volume(double value) {
    _volume = value.clamp(0.0, 1.0);
    unawaited(_player.setVolume(_volume));
  }

  /// Mic capture window (ms). Shorter = lower latency; silence-aware Live
  /// capture is handled natively in [PlaybackCaptureService].
  static const int chunkMs = 1200;
  static const _backoff = Duration(seconds: 1);
  /// Let speaker audio die out so the next mic chunk does not re-hear TTS.
  static const _postTtsCooldown = Duration(milliseconds: 250);
  static const _ocrDedupeWindow = Duration(milliseconds: 1200);

  /// Serializes TTS so overlapping Live chunks do not stomp each other.
  Future<void> _ttsChain = Future<void>.value();

  /// Live Clone: accumulate next N seconds of playback WAV PCM.
  Completer<Uint8List?>? _cloneCompleter;
  DateTime? _cloneDeadline;
  final _clonePcm = BytesBuilder(copy: false);
  _WavFormat? _cloneFormat;
  Timer? _cloneTimeout;

  /// Mic loop yields while overlay/home clone records a dedicated sample.
  bool _pausingForClone = false;

  /// Capture [seconds] of **source** audio for voice cloning (not TTS output).
  ///
  /// Playback: buffers the next N seconds of app-audio WAV.
  /// Microphone: pauses the STT loop and records N seconds from the mic.
  /// Screen OCR: returns null (no audio path).
  Future<Uint8List?> captureCloneSample({required int seconds}) async {
    if (!_running || seconds < 1) return null;
    if (_captureSource == CaptureSource.screen) return null;
    if (_cloneCompleter != null || _pausingForClone) return null;

    final wasMuted = _muted;
    _muted = true;
    try {
      await _player.stop();
    } catch (_) {}

    try {
      switch (_captureSource) {
        case CaptureSource.playback:
          return await _capturePlaybackCloneSample(seconds);
        case CaptureSource.microphone:
          return await _captureMicCloneSample(seconds);
        case CaptureSource.screen:
          return null;
      }
    } finally {
      _muted = wasMuted;
    }
  }

  Future<Uint8List?> _capturePlaybackCloneSample(int seconds) async {
    final completer = Completer<Uint8List?>();
    _cloneCompleter = completer;
    _cloneDeadline = DateTime.now().add(Duration(seconds: seconds));
    _clonePcm.clear();
    _cloneFormat = null;
    _cloneTimeout?.cancel();
    _cloneTimeout = Timer(Duration(seconds: seconds + 3), () {
      _finishCloneCapture();
    });
    return completer.future;
  }

  Future<Uint8List?> _captureMicCloneSample(int seconds) async {
    _pausingForClone = true;
    try {
      try {
        await _record.stop();
      } catch (_) {}
      // Let the mic loop notice the pause before we take the recorder.
      await Future<void>.delayed(const Duration(milliseconds: 80));
      if (!_running) return null;

      final dir = await getTemporaryDirectory();
      final path =
          '${dir.path}/overlay_clone_${DateTime.now().millisecondsSinceEpoch}.wav';
      await _record.start(
        const RecordConfig(
          encoder: AudioEncoder.wav,
          sampleRate: 16000,
          numChannels: 1,
          audioInterruption: AudioInterruptionMode.none,
          echoCancel: true,
          noiseSuppress: true,
          autoGain: true,
          androidConfig: AndroidRecordConfig(
            manageBluetooth: false,
            audioSource: AndroidAudioSource.voiceRecognition,
            audioManagerMode: AudioManagerMode.modeNormal,
          ),
        ),
        path: path,
      );
      await Future<void>.delayed(Duration(seconds: seconds));
      await _record.stop();
      if (!_running) return null;
      final file = File(path);
      final bytes = await file.readAsBytes();
      try {
        await file.delete();
      } catch (_) {}
      // ~0.5s of 16 kHz mono PCM16 (+ WAV header) as a sanity floor.
      if (bytes.length < 8000) return null;
      return Uint8List.fromList(bytes);
    } catch (_) {
      try {
        await _record.stop();
      } catch (_) {}
      return null;
    } finally {
      _pausingForClone = false;
    }
  }

  void _offerCloneWav(List<int> bytes) {
    if (_cloneDeadline == null || _cloneCompleter == null) return;
    final parsed = _parseWav(
      bytes is Uint8List ? bytes : Uint8List.fromList(bytes),
    );
    if (parsed != null) {
      final fmt = _cloneFormat;
      if (fmt == null) {
        _cloneFormat = parsed.format;
        _clonePcm.add(parsed.pcm);
      } else if (fmt.sampleRate == parsed.format.sampleRate &&
          fmt.channels == parsed.format.channels &&
          fmt.bitsPerSample == parsed.format.bitsPerSample) {
        _clonePcm.add(parsed.pcm);
      }
    }
    if (DateTime.now().isAfter(_cloneDeadline!)) {
      _finishCloneCapture();
    }
  }

  void _finishCloneCapture() {
    _cloneTimeout?.cancel();
    _cloneTimeout = null;
    final completer = _cloneCompleter;
    if (completer == null || completer.isCompleted) {
      _cloneDeadline = null;
      _cloneCompleter = null;
      _clonePcm.clear();
      _cloneFormat = null;
      return;
    }
    final wav = _buildCloneWav();
    _cloneDeadline = null;
    _cloneCompleter = null;
    _clonePcm.clear();
    _cloneFormat = null;
    completer.complete(wav);
  }

  void _abortCloneCapture() {
    _cloneTimeout?.cancel();
    _cloneTimeout = null;
    _cloneDeadline = null;
    final completer = _cloneCompleter;
    _cloneCompleter = null;
    _clonePcm.clear();
    _cloneFormat = null;
    _pausingForClone = false;
    if (completer != null && !completer.isCompleted) {
      completer.complete(null);
    }
  }

  Uint8List? _buildCloneWav() {
    final fmt = _cloneFormat;
    final pcm = _clonePcm.takeBytes();
    if (fmt == null || pcm.isEmpty) return null;
    // Reject near-silent / too-short samples (~0.4s of PCM).
    final bytesPerSec =
        fmt.sampleRate * fmt.channels * (fmt.bitsPerSample ~/ 8);
    if (bytesPerSec <= 0 || pcm.length < (bytesPerSec * 0.4).round()) {
      return null;
    }
    return _writeWav(pcm: pcm, format: fmt);
  }

  Future<bool> start({
    CaptureSource source = CaptureSource.microphone,
  }) async {
    if (_running) return true;
    if (!await _auth.hasTokens()) return false;
    await _voiceProfiles.init();
    _captureSource = source;
    if (source == CaptureSource.microphone) {
      if (!await _record.hasPermission()) {
        _statusController.add('Microphone permission denied');
        return false;
      }
    } else {
      if (!await PlaybackCapture.isSupported()) {
        _statusController.add('Live capture needs Android 10+');
        return false;
      }
      final consented = await PlaybackCapture.requestConsent();
      if (!consented) {
        _statusController.add('Screen/audio capture permission denied');
        return false;
      }
      final mode = source == CaptureSource.screen
          ? PlaybackCaptureMode.screen
          : PlaybackCaptureMode.audio;
      final started = await PlaybackCapture.start(mode: mode);
      if (!started) {
        _statusController.add(
          source == CaptureSource.screen
              ? 'Could not start screen capture'
              : 'Could not start app audio capture',
        );
        return false;
      }
      if (source == CaptureSource.screen) {
        try {
          _screenOcr = ScreenOcr(sourceLanguage: _sourceLanguage);
        } catch (e) {
          await PlaybackCapture.stop();
          _statusController.add('Screen OCR unavailable on this device');
          return false;
        }
        _lastOcrNormalized = '';
        _lastOcrEmittedAt = null;
      }
    }
    await _ensureAudioSession();
    _running = true;
    _statusController.add('Starting…');
    unawaited(_runLoop().catchError((e, s) {
      if (_running) _statusController.add('Error: $e');
    }));
    return true;
  }

  /// Prefer ducking YouTube/media instead of pausing it when we listen or speak.
  /// TTS uses [AndroidAudioUsage.assistant] so it stays audible (accessibility
  /// stream is often muted) while still excluded from AudioPlaybackCapture.
  Future<void> _ensureAudioSession() async {
    if (_audioSessionReady) return;
    try {
      final session = await AudioSession.instance;
      await session.configure(
        AudioSessionConfiguration(
          avAudioSessionCategory: AVAudioSessionCategory.playAndRecord,
          avAudioSessionCategoryOptions:
              AVAudioSessionCategoryOptions.mixWithOthers |
                  AVAudioSessionCategoryOptions.defaultToSpeaker |
                  AVAudioSessionCategoryOptions.allowBluetooth,
          avAudioSessionMode: AVAudioSessionMode.spokenAudio,
          avAudioSessionRouteSharingPolicy:
              AVAudioSessionRouteSharingPolicy.defaultPolicy,
          androidAudioAttributes: const AndroidAudioAttributes(
            contentType: AndroidAudioContentType.speech,
            flags: AndroidAudioFlags.none,
            usage: AndroidAudioUsage.assistant,
          ),
          androidAudioFocusGainType:
              AndroidAudioFocusGainType.gainTransientMayDuck,
          androidWillPauseWhenDucked: false,
        ),
      );
      await session.setActive(true);
      _audioSessionReady = true;
    } catch (_) {
      // Still run without session tweaks if configuration fails.
    }
  }

  Future<void> stop() async {
    _running = false;
    _playbackActive = false;
    _abortCloneCapture();
    await _playbackSub?.cancel();
    _playbackSub = null;
    if (_captureSource == CaptureSource.playback ||
        _captureSource == CaptureSource.screen) {
      await PlaybackCapture.stop();
    }
    final ocr = _screenOcr;
    _screenOcr = null;
    if (ocr != null) {
      await ocr.close();
    }
    try {
      await _record.stop();
    } catch (_) {}
    await _player.stop();
  }

  void dispose() {
    _running = false;
    _abortCloneCapture();
    unawaited(_playbackSub?.cancel());
    _playbackSub = null;
    unawaited(PlaybackCapture.stop());
    final ocr = _screenOcr;
    _screenOcr = null;
    if (ocr != null) {
      unawaited(ocr.close());
    }
    try {
      _record.dispose();
    } catch (_) {}
    _player.dispose();
    _statusController.close();
    _paywallController.close();
    _sourceTextController.close();
    _translatedTextController.close();
    unawaited(_voiceProfiles.dispose());
  }

  Future<void> _runLoop() async {
    switch (_captureSource) {
      case CaptureSource.playback:
        await _runPlaybackLoop();
      case CaptureSource.screen:
        await _runScreenLoop();
      case CaptureSource.microphone:
        await _runMicLoop();
    }
  }

  Future<void> _runPlaybackLoop() async {
    final queue = StreamController<List<int>>();
    _playbackSub = PlaybackCapture.audioStream.listen(
      queue.add,
      onError: (Object e) {
        if (_running) {
          _statusController.add('Capture error: $e');
        }
      },
    );
    _statusController.add('Listening to app audio…');
    // Keep only the newest chunk so we stay near real-time instead of
    // translating audio that is many seconds old.
    List<int>? latest;
    var pumping = false;

    Future<void> pump() async {
      if (pumping) return;
      pumping = true;
      try {
        while (_running) {
          final bytes = latest;
          latest = null;
          if (bytes == null) break;
          await _processWavBytes(
            bytes,
            emptySpeechMessage:
                'No speech in app audio — play media with dialogue',
            // TTS uses assistant usage (excluded from capture); do not block
            // the next STT/translate on playback finishing.
            awaitTts: false,
          );
        }
      } finally {
        pumping = false;
        if (_running && latest != null) {
          unawaited(pump());
        }
      }
    }

    try {
      await for (final bytes in queue.stream) {
        if (!_running) break;
        _offerCloneWav(bytes);
        latest = bytes;
        unawaited(pump());
      }
    } finally {
      await queue.close();
      await _playbackSub?.cancel();
      _playbackSub = null;
      _finishCloneCapture();
    }
  }

  Future<void> _runScreenLoop() async {
    final ocr = _screenOcr;
    if (ocr == null) return;
    final queue = StreamController<Uint8List>();
    _playbackSub = PlaybackCapture.frameStream.listen(
      queue.add,
      onError: (Object e) {
        if (_running) {
          _statusController.add('Capture error: $e');
        }
      },
    );
    _statusController.add('Reading on-screen text…');
    Uint8List? latest;
    var pumping = false;

    Future<void> pump() async {
      if (pumping) return;
      pumping = true;
      try {
        while (_running) {
          final jpeg = latest;
          latest = null;
          if (jpeg == null) break;
          try {
            _statusController.add('Reading screen…');
            final raw = await ocr.recognizeJpeg(jpeg);
            if (!_running) break;
            final text = stripNonVerbal(raw);
            if (text.isEmpty) {
              _statusController.add('No text on screen');
              continue;
            }
            final normalized = _normalizeOcrText(text);
            final now = DateTime.now();
            final lastAt = _lastOcrEmittedAt;
            if (normalized == _lastOcrNormalized &&
                lastAt != null &&
                now.difference(lastAt) < _ocrDedupeWindow) {
              continue;
            }
            if (normalized == _lastOcrNormalized) {
              continue;
            }
            _lastOcrNormalized = normalized;
            _lastOcrEmittedAt = now;
            await _processSourceText(text, awaitTts: false);
          } catch (e) {
            if (_running) {
              if (e is DioException && e.error is QuotaExceededException) {
                _statusController.add('Upgrade required');
                _paywallController.add(null);
              } else {
                _statusController.add(_formatPipelineError(e));
              }
            }
          }
        }
      } finally {
        pumping = false;
        if (_running && latest != null) {
          unawaited(pump());
        }
      }
    }

    try {
      await for (final jpeg in queue.stream) {
        if (!_running) break;
        latest = jpeg;
        unawaited(pump());
      }
    } finally {
      await queue.close();
      await _playbackSub?.cancel();
      _playbackSub = null;
    }
  }

  String _normalizeOcrText(String text) =>
      text.toLowerCase().replaceAll(RegExp(r'\s+'), ' ').trim();

  Future<void> _runMicLoop() async {
    while (_running) {
      String? path;
      try {
        // Yield while overlay/home clone takes a dedicated mic sample.
        while (_running && _pausingForClone) {
          await Future<void>.delayed(const Duration(milliseconds: 50));
        }
        if (!_running) break;

        // Never capture while TTS is on the speaker (feedback loop).
        while (_running && _playbackActive) {
          await Future<void>.delayed(const Duration(milliseconds: 50));
        }
        if (!_running) break;
        if (_pausingForClone) continue;

        _statusController.add('Listening…');
        path = await _recordToFile();
        if (!_running || path == null) {
          await Future<void>.delayed(_backoff);
          continue;
        }
        final pathToDelete = path;
        try {
          final bytes = await _readFileBytes(pathToDelete);
          if (bytes.isEmpty) {
            _statusController.add('No audio captured');
            await Future<void>.delayed(_backoff);
            continue;
          }
          await _processWavBytes(
            bytes,
            emptySpeechMessage: 'No speech detected — speak near the mic',
          );
        } finally {
          try {
            await File(pathToDelete).delete();
          } catch (_) {}
        }
      } catch (e) {
        if (_running) {
          if (e is DioException && e.error is QuotaExceededException) {
            _statusController.add('Upgrade required');
            _paywallController.add(null);
          } else {
            _statusController.add(_formatPipelineError(e));
          }
        }
        await Future<void>.delayed(_backoff);
      }
    }
  }

  Future<void> _processWavBytes(
    List<int> bytes, {
    required String emptySpeechMessage,
    bool awaitTts = true,
  }) async {
    try {
      // Match speaker before STT so TTS voice tracks the talker (desktop parity).
      final samples = decodeWavToFloats(bytes);
      final voiceId = samples == null
          ? _voiceId
          : _voiceProfiles.resolveVoiceIdForAudio(
              samples,
              fallbackVoiceId: _voiceId,
            );

      _statusController.add('Transcribing…');
      final text = stripNonVerbal(await _transcribe(bytes));
      if (!_running) return;
      if (text.isEmpty) {
        _statusController.add(emptySpeechMessage);
        return;
      }
      await _processSourceText(text, awaitTts: awaitTts, voiceId: voiceId);
    } catch (e) {
      if (!_running) return;
      if (e is DioException && e.error is QuotaExceededException) {
        _statusController.add('Upgrade required');
        _paywallController.add(null);
      } else {
        _statusController.add(_formatPipelineError(e));
      }
    }
  }

  Future<void> _processSourceText(
    String text, {
    bool awaitTts = true,
    String? voiceId,
  }) async {
    final ttsVoice = (voiceId != null && voiceId.isNotEmpty) ? voiceId : _voiceId;
    try {
      _sourceTextController.add(text);
      _statusController.add('Translating…');
      final translated = stripNonVerbal(await _translate(text));
      if (translated.isEmpty || !_running) return;
      _translatedTextController.add(translated);
      if (_muted) {
        _statusController.add('Muted');
        return;
      }
      _statusController.add('Speaking…');
      if (awaitTts) {
        await _synthesizeAndPlay(translated, voiceId: ttsVoice);
      } else {
        _ttsChain = _ttsChain
            .then((_) async {
              if (!_running || _muted) return;
              await _synthesizeAndPlay(translated, voiceId: ttsVoice);
            })
            .catchError((Object _) {});
      }
    } catch (e) {
      if (!_running) return;
      if (e is DioException && e.error is QuotaExceededException) {
        _statusController.add('Upgrade required');
        _paywallController.add(null);
      } else {
        _statusController.add(_formatPipelineError(e));
      }
    }
  }

  Future<String?> _recordToFile() async {
    try {
      final dir = await getTemporaryDirectory();
      // WAV (PCM16 + headers) — ElevenLabs STT rejects bare .pcm / audio.raw.
      final path =
          '${dir.path}/mic_chunk_${DateTime.now().millisecondsSinceEpoch}.wav';
      await _record.start(
        const RecordConfig(
          encoder: AudioEncoder.wav,
          sampleRate: 16000,
          numChannels: 1,
          // Keep YouTube/other media playing while we listen (Live Translate).
          audioInterruption: AudioInterruptionMode.none,
          // Reduce speaker→mic feedback when TTS plays through the device.
          echoCancel: true,
          noiseSuppress: true,
          autoGain: true,
          androidConfig: AndroidRecordConfig(
            // SCO headset routing often interrupts media playback.
            manageBluetooth: false,
            audioSource: AndroidAudioSource.voiceRecognition,
            audioManagerMode: AudioManagerMode.modeNormal,
          ),
        ),
        path: path,
      );
      await Future<void>.delayed(const Duration(milliseconds: chunkMs));
      await _record.stop();
      return path;
    } catch (e) {
      if (_running) {
        _statusController.add('Mic error: $e');
      }
      return null;
    }
  }

  Future<List<int>> _readFileBytes(String path) async {
    final file = File(path);
    if (!await file.exists()) return [];
    return file.readAsBytes();
  }

  Future<String> _transcribe(List<int> bytes) async {
    final r = await _api.transcribe(bytes, language: _sourceLanguage);
    return (r['text'] as String?)?.trim() ?? '';
  }

  Future<String> _translate(String text) async {
    final r = await _api.translate(
      text: text,
      targetLanguage: _targetLanguage,
      sourceLanguage: _sourceLanguage,
    );
    return (r['translated_text'] as String?)?.trim() ?? '';
  }

  Future<void> _synthesizeAndPlay(
    String text, {
    required String voiceId,
  }) async {
    if (_muted || !_running) return;
    final id = voiceId.isNotEmpty ? voiceId : _voiceId;
    final bytes = await _api.synthesize(text: text, voiceId: id);
    if (bytes.isEmpty || !_running || _muted) return;
    final dir = await getTemporaryDirectory();
    final path = '${dir.path}/tts_${DateTime.now().millisecondsSinceEpoch}.mp3';
    final file = File(path);
    await file.writeAsBytes(bytes);
    _playbackActive = true;
    try {
      // Ensure mic is not open while speaker TTS is playing.
      try {
        await _record.stop();
      } catch (_) {}
      try {
        final session = await AudioSession.instance;
        await session.setActive(true);
      } catch (_) {}
      await _player.stop();
      await _player.setFilePath(path);
      await _player.setVolume(_volume <= 0 ? 1.0 : _volume);
      await _player.play();
      await _player.playerStateStream.firstWhere((s) =>
          s.processingState == ProcessingState.completed ||
          s.processingState == ProcessingState.idle);
      if (_running) {
        await Future<void>.delayed(_postTtsCooldown);
      }
    } finally {
      _playbackActive = false;
      try {
        await file.delete();
      } catch (_) {}
    }
  }
}

class _WavFormat {
  const _WavFormat({
    required this.sampleRate,
    required this.channels,
    required this.bitsPerSample,
  });

  final int sampleRate;
  final int channels;
  final int bitsPerSample;
}

class _ParsedWav {
  const _ParsedWav({required this.format, required this.pcm});

  final _WavFormat format;
  final Uint8List pcm;
}

_ParsedWav? _parseWav(Uint8List bytes) {
  if (bytes.length < 44) return null;
  if (bytes[0] != 0x52 || bytes[1] != 0x49 || bytes[2] != 0x46 || bytes[3] != 0x46) {
    return null;
  }
  var offset = 12;
  int? sampleRate;
  int? channels;
  int? bitsPerSample;
  Uint8List? pcm;
  while (offset + 8 <= bytes.length) {
    final id = String.fromCharCodes(bytes.sublist(offset, offset + 4));
    final size = ByteData.sublistView(bytes, offset + 4, offset + 8)
        .getUint32(0, Endian.little);
    final dataStart = offset + 8;
    final dataEnd = dataStart + size;
    if (dataEnd > bytes.length) break;
    if (id == 'fmt ' && size >= 16) {
      final bd = ByteData.sublistView(bytes, dataStart, dataEnd);
      channels = bd.getUint16(2, Endian.little);
      sampleRate = bd.getUint32(4, Endian.little);
      bitsPerSample = bd.getUint16(14, Endian.little);
    } else if (id == 'data') {
      pcm = bytes.sublist(dataStart, dataEnd);
    }
    offset = dataEnd + (size.isOdd ? 1 : 0);
  }
  if (sampleRate == null ||
      channels == null ||
      bitsPerSample == null ||
      pcm == null ||
      pcm.isEmpty) {
    return null;
  }
  return _ParsedWav(
    format: _WavFormat(
      sampleRate: sampleRate,
      channels: channels,
      bitsPerSample: bitsPerSample,
    ),
    pcm: pcm,
  );
}

Uint8List _writeWav({
  required Uint8List pcm,
  required _WavFormat format,
}) {
  final byteRate =
      format.sampleRate * format.channels * (format.bitsPerSample ~/ 8);
  final blockAlign = format.channels * (format.bitsPerSample ~/ 8);
  final dataSize = pcm.length;
  final out = BytesBuilder(copy: false);
  void writeString(String s) => out.add(s.codeUnits);
  void writeUint32(int v) {
    final b = ByteData(4)..setUint32(0, v, Endian.little);
    out.add(b.buffer.asUint8List());
  }

  void writeUint16(int v) {
    final b = ByteData(2)..setUint16(0, v, Endian.little);
    out.add(b.buffer.asUint8List());
  }

  writeString('RIFF');
  writeUint32(36 + dataSize);
  writeString('WAVE');
  writeString('fmt ');
  writeUint32(16);
  writeUint16(1); // PCM
  writeUint16(format.channels);
  writeUint32(format.sampleRate);
  writeUint32(byteRate);
  writeUint16(blockAlign);
  writeUint16(format.bitsPerSample);
  writeString('data');
  writeUint32(dataSize);
  out.add(pcm);
  return out.takeBytes();
}

String _formatPipelineError(Object e) {
  if (e is DioException) {
    final code = e.response?.statusCode;
    final data = e.response?.data;
    String? detail;
    if (data is Map && data['detail'] != null) {
      final d = data['detail'];
      detail = d is Map ? (d['error']?.toString() ?? d.toString()) : d.toString();
    } else if (data is String && data.isNotEmpty) {
      detail = data.length > 160 ? '${data.substring(0, 160)}…' : data;
    }
    if (code != null && detail != null) return 'Error $code: $detail';
    if (code != null) return 'Error $code from API';
    if (e.type == DioExceptionType.connectionTimeout ||
        e.type == DioExceptionType.receiveTimeout ||
        e.type == DioExceptionType.connectionError) {
      return 'Network error — check connection / API';
    }
  }
  return 'Error: $e';
}

/// Remove ASR non-speech markers so TTS only speaks actual words.
/// Mirrors desktop [strip_non_verbal] (brackets, parenthetical markers, music symbols).
String stripNonVerbal(String text) {
  if (text.isEmpty) return '';
  var result = text;
  result = result.replaceAll(_bracketMarkerRe, '');
  result = result.replaceAll(_parenMarkerRe, '');
  result = result.replaceAll(_asteriskMarkerRe, '');
  result = result.replaceAll(_musicSymbolsRe, '');
  result = result.replaceAll(_anyBracketRe, '');
  result = result.replaceAll(_emptyBracketRe, '');
  result = result.replaceAll(_ellipsisRe, '');
  result = result.replaceAll(_whitespaceRe, ' ').trim();
  if (result.isNotEmpty && !_speechRe.hasMatch(result)) {
    return '';
  }
  return result;
}

const _markerBody =
    r'music|pause|laughter|laughing|applause|silence|inaudible|crosstalk'
    r'|background\s*noise|noise|sigh|sighing|cough|coughing|gasp|gasping'
    r'|crying|sobbing|sniffing|clearing\s*throat|breathing|exhale|inhale'
    r'|foreign|foreign\s*language|unintelligible|indiscernible'
    r'|blank_audio|no\s*speech|beep|bleep|censored'
    r'|phone\s*ringing|doorbell|alarm|static'
    r'|sound\s*effect|sfx|fx'
    r'|crowd|cheering|booing|clapping'
    r'|singing|humming|whistling'
    r'|playing|instrumental'
    r'|intro|outro|transition'
    r'|video\s*playing|audio\s*playing';

final _bracketMarkerRe =
    RegExp(r'\[' + _markerBody + r'\]', caseSensitive: false);
final _parenMarkerRe =
    RegExp(r'\(' + _markerBody + r'\)', caseSensitive: false);
final _asteriskMarkerRe = RegExp(
  r'\*(?:music|pause|laughter|laughing|applause|silence|sigh|cough'
  r'|crying|singing|humming|whistling|gasps?|laughs?|sighs?|coughs?)\*',
  caseSensitive: false,
);
final _musicSymbolsRe = RegExp(r'[♪♫]+');
final _anyBracketRe = RegExp(r'\[[^\]]{0,50}\]');
final _emptyBracketRe = RegExp(r'\[\s*\]|\(\s*\)');
final _ellipsisRe = RegExp(r'\.{3,}');
final _whitespaceRe = RegExp(r'\s{2,}');
final _speechRe = RegExp(r'[0-9A-Za-z\u00C0-\u024F\u0400-\u04FF\u3040-\u30FF\u3400-\u9FFF\uAC00-\uD7AF]');
