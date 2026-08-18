import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

/// Capture mode for [PlaybackCapture.start].
enum PlaybackCaptureMode {
  /// App/system playback → WAV chunks.
  audio,

  /// Screen frames → JPEG chunks for OCR.
  screen,
}

/// Android MediaProjection → WAV (audio) or JPEG frames (screen) for Live Translate.
class PlaybackCapture {
  PlaybackCapture._();

  static const _method =
      MethodChannel('app.livetranslate.live_translate_mobile/playback_capture');
  static const _audioEvents = EventChannel(
    'app.livetranslate.live_translate_mobile/playback_capture/audio',
  );
  static const _frameEvents = EventChannel(
    'app.livetranslate.live_translate_mobile/playback_capture/frames',
  );

  static StreamSubscription<dynamic>? _audioSub;
  static StreamSubscription<dynamic>? _frameSub;
  static final _audioController = StreamController<Uint8List>.broadcast();
  static final _frameController = StreamController<Uint8List>.broadcast();

  static bool get _supportedPlatform =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.android;

  /// WAV chunks (silence-flush ~0.5–2s, 16 kHz mono PCM) while audio capture runs.
  static Stream<Uint8List> get audioStream => _audioController.stream;

  /// JPEG screen frames (~0.9s) while screen capture is running.
  static Stream<Uint8List> get frameStream => _frameController.stream;

  static Future<bool> isSupported() async {
    if (!_supportedPlatform) return false;
    try {
      final ok = await _method.invokeMethod<bool>('isSupported');
      return ok == true;
    } on PlatformException {
      return false;
    }
  }

  /// Shows the system screen/audio capture consent dialog.
  static Future<bool> requestConsent() async {
    if (!_supportedPlatform) return false;
    try {
      final ok = await _method.invokeMethod<bool>('requestConsent');
      return ok == true;
    } on PlatformException catch (e) {
      if (e.code == 'denied') return false;
      rethrow;
    }
  }

  static Future<bool> start({
    PlaybackCaptureMode mode = PlaybackCaptureMode.audio,
  }) async {
    if (!_supportedPlatform) return false;
    if (mode == PlaybackCaptureMode.screen) {
      await _ensureFrameListening();
    } else {
      await _ensureAudioListening();
    }
    try {
      await _method.invokeMethod<void>('start', {
        'mode': mode == PlaybackCaptureMode.screen ? 'screen' : 'audio',
      });
      return true;
    } on PlatformException {
      return false;
    }
  }

  static Future<void> stop() async {
    try {
      await _method.invokeMethod<void>('stop');
    } on PlatformException {
      // ignore
    }
    await _audioSub?.cancel();
    await _frameSub?.cancel();
    _audioSub = null;
    _frameSub = null;
  }

  static Future<void> _ensureAudioListening() async {
    if (_audioSub != null) return;
    _audioSub = _audioEvents.receiveBroadcastStream().listen(
      (event) {
        final bytes = _asBytes(event);
        if (bytes != null) _audioController.add(bytes);
      },
      onError: (Object e, StackTrace st) {
        if (!_audioController.isClosed) {
          _audioController.addError(e, st);
        }
      },
    );
  }

  static Future<void> _ensureFrameListening() async {
    if (_frameSub != null) return;
    _frameSub = _frameEvents.receiveBroadcastStream().listen(
      (event) {
        final bytes = _asBytes(event);
        if (bytes != null) _frameController.add(bytes);
      },
      onError: (Object e, StackTrace st) {
        if (!_frameController.isClosed) {
          _frameController.addError(e, st);
        }
      },
    );
  }

  static Uint8List? _asBytes(dynamic event) {
    if (event is Uint8List) return event;
    if (event is List<int>) return Uint8List.fromList(event);
    if (event is ByteBuffer) return event.asUint8List();
    return null;
  }
}
