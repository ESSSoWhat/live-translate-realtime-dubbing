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
    this.targetLanguage = 'es',
    this.voiceId = '21m00Tcm4TlvDq8ikWAM',
  });

  final String targetLanguage;
  final String voiceId;

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

  /// When true, capture/translate continue but TTS is skipped.
  bool get muted => _muted;

  set muted(bool value) {
    _muted = value;
    if (value) {
      unawaited(_player.stop());
    }
  }

  static const int chunkSeconds = 3;
  static const _backoff = Duration(seconds: 1);

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
            await Future<void>.delayed(_backoff);
            continue;
          }
          _statusController.add('Transcribing…');
          final text = await _transcribe(bytes);
          if (text.isEmpty || !_running) {
            await Future<void>.delayed(_backoff);
            continue;
          }
          _sourceTextController.add(text);
          _statusController.add('Translating…');
          final translated = await _translate(text);
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
            _statusController.add('Error: $e');
          }
        }
        await Future<void>.delayed(_backoff);
      }
    }
  }

  Future<String?> _recordToFile() async {
    try {
      final dir = await getTemporaryDirectory();
      final path = '${dir.path}/mic_chunk_${DateTime.now().millisecondsSinceEpoch}.pcm';
      await _record.start(
        const RecordConfig(
          encoder: AudioEncoder.pcm16bits,
          sampleRate: 16000,
          numChannels: 1,
        ),
        path: path,
      );
      await Future<void>.delayed(Duration(seconds: chunkSeconds));
      await _record.stop();
      return path;
    } catch (_) {
      return null;
    }
  }

  Future<List<int>> _readFileBytes(String path) async {
    final file = File(path);
    if (!await file.exists()) return [];
    return file.readAsBytes();
  }

  Future<String> _transcribe(List<int> bytes) async {
    final r = await _api.transcribe(bytes, language: 'auto');
    return (r['text'] as String?)?.trim() ?? '';
  }

  Future<String> _translate(String text) async {
    final r = await _api.translate(
      text: text,
      targetLanguage: targetLanguage,
      sourceLanguage: 'auto',
    );
    return (r['translated_text'] as String?)?.trim() ?? '';
  }

  Future<void> _synthesizeAndPlay(String text) async {
    if (_muted || !_running) return;
    final bytes = await _api.synthesize(text: text, voiceId: voiceId);
    if (bytes.isEmpty || !_running || _muted) return;
    final dir = await getTemporaryDirectory();
    final path = '${dir.path}/tts_${DateTime.now().millisecondsSinceEpoch}.mp3';
    final file = File(path);
    await file.writeAsBytes(bytes);
    try {
      await _player.setFilePath(path);
      await _player.play();
      await _player.playerStateStream
          .firstWhere((s) =>
              s.processingState == ProcessingState.completed ||
              s.processingState == ProcessingState.idle);
    } finally {
      try {
        await file.delete();
      } catch (_) {}
    }
  }
}
