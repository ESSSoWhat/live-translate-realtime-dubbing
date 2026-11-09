import 'dart:io';
import 'dart:typed_data';

import 'package:google_mlkit_text_recognition/google_mlkit_text_recognition.dart';
import 'package:path_provider/path_provider.dart';

/// On-device OCR for Live Translate screen JPEG frames.
class ScreenOcr {
  ScreenOcr({String sourceLanguage = 'auto'})
      : _recognizer = TextRecognizer(script: scriptForLanguage(sourceLanguage));

  final TextRecognizer _recognizer;
  bool _closed = false;

  static TextRecognitionScript scriptForLanguage(String code) {
    switch (code) {
      case 'ja':
        return TextRecognitionScript.japanese;
      case 'ko':
        return TextRecognitionScript.korean;
      case 'zh':
        return TextRecognitionScript.chinese;
      case 'hi':
        return TextRecognitionScript.devanagiri;
      default:
        // auto / en / es / etc. — latin model covers most European scripts.
        return TextRecognitionScript.latin;
    }
  }

  /// Recognize visible text from a JPEG byte buffer.
  Future<String> recognizeJpeg(Uint8List jpegBytes) async {
    if (_closed || jpegBytes.isEmpty) return '';
    final dir = await getTemporaryDirectory();
    final path =
        '${dir.path}/lt_ocr_${DateTime.now().microsecondsSinceEpoch}.jpg';
    final file = File(path);
    try {
      await file.writeAsBytes(jpegBytes, flush: true);
      final recognized =
          await _recognizer.processImage(InputImage.fromFilePath(path));
      return _joinText(recognized);
    } catch (_) {
      // Native ML Kit failures on some emulator ABIs should not kill the loop.
      return '';
    } finally {
      try {
        await file.delete();
      } catch (_) {}
    }
  }

  String _joinText(RecognizedText recognized) {
    final buffer = StringBuffer();
    for (final block in recognized.blocks) {
      final t = block.text.trim();
      if (t.isEmpty) continue;
      if (buffer.isNotEmpty) buffer.writeln();
      buffer.write(t);
    }
    return buffer.toString().trim();
  }

  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    await _recognizer.close();
  }
}
