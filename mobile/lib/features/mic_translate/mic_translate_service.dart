import 'dart:async';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:just_audio/just_audio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

import '../../services/api_client.dart';
import '../../services/auth_service.dart';

/// Captures mic in chunks, sends to backend (transcribe → translate → synthesize), plays result.
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
  final _player = AudioPlayer();
  final _statusController = StreamController<String>.broadcast();
  final _paywallController = StreamController<void>.broadcast();
  final _sourceTextController = StreamController<String>.broadcast();
  final _translatedTextController = StreamController<String>.broadcast();

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

  static const int chunkSeconds = 3;
  static const _backoff = Duration(seconds: 1);
  /// Let speaker audio die out so the next mic chunk does not re-hear TTS.
  static const _postTtsCooldown = Duration(milliseconds: 500);

  Future<bool> start() async {
    if (_running) return true;
    if (!await _auth.hasTokens()) return false;
    if (!await _record.hasPermission()) {
      _statusController.add('Microphone permission denied');
      return false;
    }
    _running = true;
    _statusController.add('Starting…');
    unawaited(_runLoop().catchError((e, s) {
      if (_running) _statusController.add('Error: $e');
    }));
    return true;
  }

  Future<void> stop() async {
    _running = false;
    _playbackActive = false;
    await _record.stop();
    await _player.stop();
  }

  void dispose() {
    _record.dispose();
    _player.dispose();
    _statusController.close();
    _paywallController.close();
    _sourceTextController.close();
    _translatedTextController.close();
  }

  Future<void> _runLoop() async {
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
          _statusController.add('Transcribing…');
          // Strip [MUSIC], (laughter), etc. — TTS speaks speech only.
          final text = stripNonVerbal(await _transcribe(bytes));
          if (!_running) {
            await Future<void>.delayed(_backoff);
            continue;
          }
          if (text.isEmpty) {
            _statusController.add('No speech detected — speak near the mic');
            await Future<void>.delayed(_backoff);
            continue;
          }
          _sourceTextController.add(text);
          _statusController.add('Translating…');
          final translated = stripNonVerbal(await _translate(text));
          if (translated.isEmpty || !_running) {
            await Future<void>.delayed(_backoff);
            continue;
          }
          _translatedTextController.add(translated);
          if (_muted) {
            _statusController.add('Muted');
            continue;
          }
          _statusController.add('Speaking…');
          await _synthesizeAndPlay(translated);
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
          // Reduce speaker→mic feedback when TTS plays through the device.
          echoCancel: true,
          noiseSuppress: true,
          autoGain: true,
        ),
        path: path,
      );
      await Future<void>.delayed(Duration(seconds: chunkSeconds));
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
      await _player.setFilePath(path);
      await _player.setVolume(_volume);
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
