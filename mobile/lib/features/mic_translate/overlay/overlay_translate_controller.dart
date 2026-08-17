import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';

import '../../../services/app_settings.dart';
import '../mic_translate_service.dart';
import 'mic_foreground_service.dart';
import 'overlay_bridge.dart';

/// Result of [OverlayTranslateController.start].
class OverlayStartResult {
  const OverlayStartResult({
    required this.started,
    required this.overlayShown,
  });

  final bool started;
  final bool overlayShown;
}

/// Orchestrates overlay permission → mic FGS → [MicTranslateService] → bubble.
class OverlayTranslateController {
  OverlayTranslateController({required this.service});

  final MicTranslateService service;

  final _activeController = StreamController<bool>.broadcast();
  StreamSubscription<String>? _statusSub;
  StreamSubscription<String>? _sourceSub;
  StreamSubscription<String>? _translatedSub;

  String _status = '';
  String _source = '';
  String _translated = '';
  bool _active = false;
  bool _overlayShown = false;
  bool _mainBridgeReady = false;

  bool get isActive => _active;
  bool get overlayShown => _overlayShown;

  /// Emits when translation starts/stops (including stop from the bubble).
  Stream<bool> get activeStream => _activeController.stream;

  static bool get _android =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.android;

  Future<OverlayStartResult> start({
    bool showOverlay = true,
    CaptureSource captureSource = CaptureSource.microphone,
  }) async {
    if (_active) {
      if (showOverlay && !_overlayShown && _android) {
        final shown = await _tryShowOverlay();
        return OverlayStartResult(started: true, overlayShown: shown);
      }
      return OverlayStartResult(started: true, overlayShown: _overlayShown);
    }

    // Mic mode: microphone FGS. Live audio/screen: MediaProjection FGS is
    // started inside [MicTranslateService.start] after consent.
    if (_android && captureSource == CaptureSource.microphone) {
      await MicForegroundService.start();
    }

    final started = await service.start(source: captureSource);
    if (!started) {
      if (_android && captureSource == CaptureSource.microphone) {
        await MicForegroundService.stop();
      }
      return const OverlayStartResult(started: false, overlayShown: false);
    }

    _active = true;
    _activeController.add(true);
    _bindServiceStreams();
    _ensureMainBridge();

    var overlayShown = false;
    if (showOverlay && _android) {
      overlayShown = await _tryShowOverlay();
    }

    return OverlayStartResult(started: true, overlayShown: overlayShown);
  }

  /// Call when the app resumes so a newly granted overlay permission can attach.
  Future<bool> retryOverlayIfNeeded() async {
    if (!_active || _overlayShown || !_android) return _overlayShown;
    return _tryShowOverlay();
  }

  /// Shows the bubble if already allowed; otherwise opens the system settings
  /// page without blocking translation (requestPermission can hang until grant).
  Future<bool> _tryShowOverlay() async {
    try {
      final granted = await FlutterOverlayWindow.isPermissionGranted();
      if (!granted) {
        // Open settings; do not await — the Future often never completes until
        // the user grants permission, which froze Home "Start translation".
        unawaited(FlutterOverlayWindow.requestPermission());
        return false;
      }
      _ensureMainBridge();
      await FlutterOverlayWindow.showOverlay(
        height: 72,
        width: 72,
        alignment: OverlayAlignment.centerRight,
        enableDrag: true,
        overlayTitle: 'Live Translate',
        overlayContent: 'Translation is running',
        flag: OverlayFlag.defaultFlag,
        positionGravity: PositionGravity.auto,
      );
      _overlayShown = true;
      // Overlay isolate registers its port asynchronously — push a few times.
      for (var i = 0; i < 5; i++) {
        await Future<void>.delayed(Duration(milliseconds: 200 * (i + 1)));
        await _pushUpdate();
      }
      return true;
    } catch (_) {
      _overlayShown = false;
      return false;
    }
  }

  Future<void> stop() async {
    if (!_active && !_overlayShown) {
      await service.stop();
      if (_android) await MicForegroundService.stop();
      return;
    }

    await _tearDownOverlay();
    await service.stop();
    if (_android) await MicForegroundService.stop();

    if (_active) {
      _active = false;
      _activeController.add(false);
    }
    _status = '';
    _source = '';
    _translated = '';
  }

  void dispose() {
    _statusSub?.cancel();
    _sourceSub?.cancel();
    _translatedSub?.cancel();
    _statusSub = null;
    _sourceSub = null;
    _translatedSub = null;
    OverlayBridge.dispose();
    _mainBridgeReady = false;
    if (!_activeController.isClosed) {
      _activeController.close();
    }
  }

  void _ensureMainBridge() {
    if (_mainBridgeReady) return;
    OverlayBridge.listenOnMain(_onOverlayMessage);
    _mainBridgeReady = true;
  }

  void _onOverlayMessage(dynamic event) {
    Map<String, dynamic>? map;
    if (event is Map) {
      map = Map<String, dynamic>.from(event);
    } else if (event is String) {
      try {
        final decoded = jsonDecode(event);
        if (decoded is Map) {
          map = Map<String, dynamic>.from(decoded);
        }
      } catch (_) {
        return;
      }
    }
    if (map == null) return;
    final type = map['type'];
    if (type == 'stop') {
      unawaited(stop());
    } else if (type == 'toggleMute') {
      service.muted = !service.muted;
      unawaited(_pushUpdate());
    } else if (type == 'ready') {
      unawaited(_pushUpdate());
    }
  }

  void _bindServiceStreams() {
    _statusSub?.cancel();
    _sourceSub?.cancel();
    _translatedSub?.cancel();
    _statusSub = service.statusStream.listen((s) {
      _status = s;
      unawaited(_pushUpdate());
    });
    _sourceSub = service.sourceTextStream.listen((s) {
      _source = s;
      unawaited(_pushUpdate());
    });
    _translatedSub = service.translatedTextStream.listen((s) {
      _translated = s;
      unawaited(_pushUpdate());
    });
  }

  Future<void> _pushUpdate() async {
    if (!_overlayShown) return;
    final payload = jsonEncode({
      'type': 'update',
      'status': _status,
      'source': _source,
      'translated': _translated,
      'muted': service.muted,
      'fontSize': AppSettings.captionFontSize,
      'opacity': AppSettings.captionOpacity,
    });
    OverlayBridge.sendToOverlay(payload);
    // Keep shareData as a secondary path for older plugin builds.
    try {
      await FlutterOverlayWindow.shareData(payload);
    } catch (_) {}
  }

  Future<void> _tearDownOverlay() async {
    _statusSub?.cancel();
    _sourceSub?.cancel();
    _translatedSub?.cancel();
    _statusSub = null;
    _sourceSub = null;
    _translatedSub = null;
    if (_overlayShown) {
      try {
        await FlutterOverlayWindow.closeOverlay();
      } catch (_) {}
      _overlayShown = false;
    }
  }
}
