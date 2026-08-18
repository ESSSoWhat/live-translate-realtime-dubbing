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
  bool _audioSessionReady = false;
  CaptureSource _captureSource = CaptureSource.microphone;
  StreamSubscription<Uint8List>? _playbackSub;
  ScreenOcr? _screenOcr;
  String _lastOcrNormalized = '';
  DateTime? _lastOcrEmittedAt;

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

  Future<bool> start({
    CaptureSource source = CaptureSource.microphone,
  }) async {
    if (_running) return true;
    if (!await _auth.hasTokens()) return false;
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
        latest = bytes;
        unawaited(pump());
      }
    } finally {
      await queue.close();
      await _playbackSub?.cancel();
      _playbackSub = null;
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
        // Never capture while TTS is on the speaker (feedback loop).
        while (_running && _playbackActive) {
          await Future<void>.delayed(const Duration(milliseconds: 50));
        }
        if (!_running) break;

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
      _statusController.add('Transcribing…');
      final text = stripNonVerbal(await _transcribe(bytes));
      if (!_running) return;
      if (text.isEmpty) {
        _statusController.add(emptySpeechMessage);
        return;
      }
      await _processSourceText(text, awaitTts: awaitTts);
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
  }) async {
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
        await _synthesizeAndPlay(translated);
      } else {
        _ttsChain = _ttsChain
            .then((_) async {
              if (!_running || _muted) return;
              await _synthesizeAndPlay(translated);
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

  Future<void> _synthesizeAndPlay(String text) async {
    if (_muted || !_running) return;
    final bytes = await _api.synthesize(text: text, voiceId: _voiceId);
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
