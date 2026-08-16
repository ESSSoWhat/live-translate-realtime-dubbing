import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

/// Starts/stops the Android microphone foreground service that keeps
/// background mic capture alive while translation runs.
class MicForegroundService {
  MicForegroundService._();

  static const _channel =
      MethodChannel('app.livetranslate.live_translate_mobile/mic_fgs');

  static bool get _supported =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.android;

  static Future<void> start() async {
    if (!_supported) return;
    try {
      await _channel.invokeMethod<void>('start');
    } on PlatformException {
      // Non-fatal: in-app translate can still run without FGS.
    }
  }

  static Future<void> stop() async {
    if (!_supported) return;
    try {
      await _channel.invokeMethod<void>('stop');
    } on PlatformException {
      // ignore
    }
  }
}
