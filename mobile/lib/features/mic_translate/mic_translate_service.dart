import 'dart:async';
import 'dart:io';

import 'package:just_audio/just_audio.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

import '../../services/api_client.dart';
import '../../services/auth_service.dart';

/// Target language and voice for translation (set from settings).
String targetLanguage = 'es';
String voiceId = '21m00Tcm4TlvDq8ikWAM'; // Rachel fallback

/// Captures mic in chunks, sends to backend (transcribe → translate → synthesize), plays result.
class MicTranslateService {
  final _api = ApiClient();
  final _auth = AuthService();
  final _record = AudioRecorder();
  final _player = AudioPlayer();
  final _statusController = StreamController<String>.broadcast();

  Stream<String> get statusStream => _statusController.stream;

  bool _running = false;
  static const int chunkSeconds = 3;

  Future<bool> start() async {
    if (_running) return true;
    if (!await _auth.hasTokens()) return false;
    if (!await _record.hasPermission()) {
      _statusController.add('Microphone permission denied');
      return false;
    }
    _running = true;
    _statusController.add('Starting…');
    _runLoop();
    return true;
  }

  Future<void> stop() async {
    _running = false;
    await _record.stop();
    await _player.stop();
  }

  Future<void> _runLoop() async {
    while (_running) {
      try {
        _statusController.add('Listening…');
        final path = await _recordToFile();
        if (!_running || path == null) continue;
        final bytes = await _readFileBytes(path);
        if (bytes.isEmpty) continue;
        _statusController.add('Transcribing…');
        final text = await _transcribe(bytes);
        if (text.isEmpty || !_running) continue;
        _statusController.add('Translating…');
        final translated = await _translate(text);
        if (translated.isEmpty || !_running) continue;
        _statusController.add('Speaking…');
        await _synthesizeAndPlay(translated);
      } catch (e) {
        if (_running) _statusController.add('Error: $e');
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
    final bytes = await _api.synthesize(text: text, voiceId: voiceId);
    if (bytes.isEmpty || !_running) return;
    final dir = await getTemporaryDirectory();
    final path = '${dir.path}/tts_${DateTime.now().millisecondsSinceEpoch}.mp3';
    final file = File(path);
    await file.writeAsBytes(bytes);
    await _player.setFilePath(path);
    await _player.play();
    await _player.playerStateStream
        .firstWhere((s) => s.processingState == ProcessingState.completed);
    await file.delete();
  }
}
