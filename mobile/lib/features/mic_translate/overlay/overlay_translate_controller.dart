import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';

import '../../../services/app_settings.dart';
import '../mic_translate_service.dart';
import 'mic_foreground_service.dart';
import 'overlay_bridge.dart';
import 'overlay_dock.dart';

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
  Timer? _pushDebounce;
  String? _lastPayload;

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
      // topLeft gravity: PositionGravity.auto snap math uses left-origin X.
      // (centerRight + auto can push the bubble off-screen / untappable.)
      await FlutterOverlayWindow.showOverlay(
        height: 72,
        width: 72,
        alignment: OverlayAlignment.topLeft,
        enableDrag: true,
        overlayTitle: 'Live Translate',
        overlayContent: 'Translation is running',
        flag: OverlayFlag.focusPointer,
        positionGravity: PositionGravity.auto,
      );
      _overlayShown = true;
      await dockOverlayToEdge(width: 72, height: 72, preferRight: true);
      // Overlay isolate registers its port asynchronously — one delayed push.
      await Future<void>.delayed(const Duration(milliseconds: 350));
      _schedulePush(immediate: true);
      return true;
    } catch (_) {
      _overlayShown = false;
      return false;
    }
  }

  Future<void> stop() async {
    _pushDebounce?.cancel();
    _pushDebounce = null;
    _lastPayload = null;
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
    _pushDebounce?.cancel();
    _pushDebounce = null;
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
      _schedulePush(immediate: true);
    } else if (type == 'ready') {
      _schedulePush(immediate: true);
    }
  }

  void _bindServiceStreams() {
    _statusSub?.cancel();
    _sourceSub?.cancel();
    _translatedSub?.cancel();
    _statusSub = service.statusStream.listen((s) {
      _status = s;
      _schedulePush();
    });
    _sourceSub = service.sourceTextStream.listen((s) {
      _source = s;
      _schedulePush();
    });
    _translatedSub = service.translatedTextStream.listen((s) {
      _translated = s;
      _schedulePush();
    });
  }

  /// Coalesce rapid status/caption events so the bubble does not flicker.
  void _schedulePush({bool immediate = false}) {
    if (!_overlayShown) return;
    _pushDebounce?.cancel();
    if (immediate) {
      unawaited(_pushUpdate());
      return;
    }
    _pushDebounce = Timer(const Duration(milliseconds: 120), () {
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
    if (payload == _lastPayload) return;
    _lastPayload = payload;

    // Prefer IsolateNameServer; only fall back to shareData if no listener yet.
    final delivered = OverlayBridge.sendToOverlay(payload);
    if (!delivered) {
      try {
        await FlutterOverlayWindow.shareData(payload);
      } catch (_) {}
    }
  }

  Future<void> _tearDownOverlay() async {
    _pushDebounce?.cancel();
    _pushDebounce = null;
    _lastPayload = null;
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
