import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';

import '../../../services/api_client.dart';
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
  final _api = ApiClient();
  StreamSubscription<String>? _statusSub;
  StreamSubscription<String>? _sourceSub;
  StreamSubscription<String>? _translatedSub;
  Timer? _pushDebounce;
  String? _lastPayload;

  String _status = '';
  String _source = '';
  String _translated = '';
  List<Map<String, String>> _voices = const [];
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
    if (!_active || !_android) return false;
    // Recover when Dart thinks the bubble is up but the native service died.
    if (_overlayShown) {
      try {
        if (await FlutterOverlayWindow.isActive()) return true;
      } catch (_) {}
      _overlayShown = false;
    }
    return _tryShowOverlay();
  }

  Future<bool> _tryShowOverlay() async {
    try {
      final granted = await FlutterOverlayWindow.isPermissionGranted();
      if (!granted) {
        unawaited(FlutterOverlayWindow.requestPermission());
        return false;
      }
      _ensureMainBridge();
      unawaited(_refreshVoices());
      // enableDrag MUST be false: native drag steals Flutter taps.
      // Initial LayoutParams treat width/height as raw px; resizeOverlay uses dp.
      const bubble = 96;
      await FlutterOverlayWindow.showOverlay(
        height: bubble,
        width: bubble,
        alignment: OverlayAlignment.centerRight,
        enableDrag: false,
        overlayTitle: 'Live Translate',
        overlayContent: 'Translation is running',
        flag: OverlayFlag.focusPointer,
        positionGravity: PositionGravity.none,
      );
      await Future<void>.delayed(const Duration(milliseconds: 250));
      // Apply true dp size so the bubble is visible on high-density screens.
      try {
        await FlutterOverlayWindow.resizeOverlay(bubble, bubble, false);
      } catch (_) {}
      await Future<void>.delayed(const Duration(milliseconds: 200));
      final active = await FlutterOverlayWindow.isActive();
      if (!active) {
        debugPrint('Overlay service not active after show; retrying once');
        await FlutterOverlayWindow.showOverlay(
          height: bubble,
          width: bubble,
          alignment: OverlayAlignment.centerRight,
          enableDrag: false,
          overlayTitle: 'Live Translate',
          overlayContent: 'Translation is running',
          flag: OverlayFlag.focusPointer,
          positionGravity: PositionGravity.none,
        );
        await Future<void>.delayed(const Duration(milliseconds: 300));
        try {
          await FlutterOverlayWindow.resizeOverlay(bubble, bubble, false);
        } catch (_) {}
      }
      _overlayShown = await FlutterOverlayWindow.isActive();
      if (_overlayShown) {
        _schedulePush(immediate: true);
      }
      return _overlayShown;
    } catch (e, st) {
      _overlayShown = false;
      debugPrint('Overlay show failed: $e\n$st');
      return false;
    }
  }

  Future<void> _refreshVoices() async {
    try {
      final list = await _api.getVoices();
      final mapped = <Map<String, String>>[];
      for (final m in list) {
        final id = (m['voice_id'] as String?)?.trim() ?? '';
        if (id.isEmpty) continue;
        mapped.add({
          'id': id,
          'name': (m['name'] as String?)?.trim().isNotEmpty == true
              ? (m['name'] as String).trim()
              : id,
        });
      }
      if (mapped.isEmpty) {
        mapped.add({
          'id': MicTranslateService.defaultVoiceId,
          'name': 'Rachel',
        });
      }
      _voices = mapped;
      _schedulePush(immediate: true);
    } catch (_) {
      if (_voices.isEmpty) {
        _voices = [
          {'id': MicTranslateService.defaultVoiceId, 'name': 'Rachel'},
        ];
      }
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
    } else if (type == 'setVolume') {
      final v = map['volume'];
      if (v is num) {
        final volume = v.toDouble().clamp(0.0, 1.0);
        service.volume = volume;
        unawaited(AppSettings.setTtsVolume(volume));
        _schedulePush(immediate: true);
      }
    } else if (type == 'setVoice') {
      final id = map['voiceId'] as String?;
      if (id != null && id.isNotEmpty) {
        service.voiceId = id;
        unawaited(AppSettings.setVoiceId(id));
        _schedulePush(immediate: true);
      }
    } else if (type == 'ready') {
      unawaited(_refreshVoices());
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
      'volume': service.volume,
      'voiceId': service.voiceId,
      'voices': _voices,
      'fontSize': AppSettings.captionFontSize,
      'opacity': AppSettings.captionOpacity,
    });
    if (payload == _lastPayload) return;
    _lastPayload = payload;

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
