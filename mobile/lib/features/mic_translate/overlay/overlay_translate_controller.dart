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
  StreamSubscription<Map<String, dynamic>>? _activitySub;
  Timer? _pushDebounce;
  String? _lastPayload;

  String _status = '';
  String _source = '';
  String _translated = '';
  String _highlightSource = '';
  String _highlightTranslated = '';
  bool _sourceLive = false;
  bool _translatedLive = false;
  List<Map<String, String>> _voices = const [];
  bool _active = false;
  bool _overlayShown = false;
  bool _mainBridgeReady = false;
  bool _cloning = false;

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
      // Already showing — don't call showOverlay again (plugin stopSelf mid-restart).
      try {
        if (await FlutterOverlayWindow.isActive()) {
          _overlayShown = true;
          _schedulePush(immediate: true);
          return true;
        }
      } catch (_) {}

      _ensureMainBridge();
      unawaited(_refreshVoices());
      // enableDrag MUST be false: native drag steals Flutter taps.
      // Size is dp (native patch converts); keep a single show call only.
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
      // Wait for the FGS + FlutterView to attach before treating as shown.
      for (var i = 0; i < 10; i++) {
        await Future<void>.delayed(const Duration(milliseconds: 150));
        try {
          if (await FlutterOverlayWindow.isActive()) {
            _overlayShown = true;
            _schedulePush(immediate: true);
            return true;
          }
        } catch (_) {}
      }
      _overlayShown = false;
      debugPrint('Overlay service did not become active after showOverlay');
      return false;
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
    _cloning = false;
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
    } else if (type == 'setOpacity') {
      final o = map['opacity'];
      if (o is num) {
        final opacity = o.toDouble().clamp(0.3, 1.0);
        unawaited(AppSettings.setCaptionOpacity(opacity));
        _schedulePush(immediate: true);
      }
    } else if (type == 'setVoice') {
      final id = map['voiceId'] as String?;
      if (id != null && id.isNotEmpty) {
        service.voiceId = id;
        unawaited(AppSettings.setVoiceId(id));
        _schedulePush(immediate: true);
      }
    } else if (type == 'liveClone') {
      unawaited(_liveClone());
    } else if (type == 'ready') {
      unawaited(_refreshVoices());
      _schedulePush(immediate: true);
    }
  }

  Future<void> _liveClone() async {
    if (!_active || _cloning) return;
    if (service.captureSource == CaptureSource.screen) {
      _status = 'Clone needs audio (not screen text)';
      _schedulePush(immediate: true);
      return;
    }

    _cloning = true;
    _status = 'Cloning…';
    _schedulePush(immediate: true);

    try {
      final seconds = AppSettings.autoCloneSeconds;
      final sample = await service.captureCloneSample(seconds: seconds);
      if (!_active) return;
      if (sample == null || sample.isEmpty) {
        _status = 'Clone failed';
        return;
      }

      final name = 'live_clone_${DateTime.now().millisecondsSinceEpoch}';
      final body = await _api.cloneVoice(
        audioBytes: sample,
        name: name,
        description: 'Cloned from Live Translate overlay',
      );
      if (!_active) return;

      final voiceId = (body['voice_id'] as String?)?.trim();
      if (voiceId == null || voiceId.isEmpty) {
        _status = 'Clone failed';
        return;
      }

      service.voiceId = voiceId;
      await AppSettings.setVoiceId(voiceId);
      await _refreshVoices();
      final display = (body['name'] as String?)?.trim();
      _status =
          'Cloned: ${display != null && display.isNotEmpty ? display : name}';
    } catch (_) {
      // Keep current voice on failure (do not reset voiceId).
      if (_active) _status = 'Clone failed';
    } finally {
      _cloning = false;
      if (_active) _schedulePush(immediate: true);
    }
  }

  void _bindServiceStreams() {
    _statusSub?.cancel();
    _sourceSub?.cancel();
    _translatedSub?.cancel();
    _activitySub?.cancel();
    _statusSub = service.statusStream.listen((s) {
      _status = s;
      _schedulePush();
    });
    _sourceSub = service.sourceTextStream.listen((s) {
      _source = s;
      _highlightSource = s;
      _schedulePush();
    });
    _translatedSub = service.translatedTextStream.listen((s) {
      _translated = s;
      _highlightTranslated = s;
      _schedulePush();
    });
    _activitySub = service.captionActivityStream.listen((activity) {
      final source = activity['source'];
      final translated = activity['translated'];
      if (source is String && source.trim().isNotEmpty) {
        _highlightSource = source.trim();
      }
      if (translated is String && translated.trim().isNotEmpty) {
        _highlightTranslated = translated.trim();
      }
      _sourceLive = activity['sourceLive'] == true;
      _translatedLive = activity['translatedLive'] == true;
      _schedulePush(immediate: true);
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
      'highlightSource': _highlightSource,
      'highlightTranslated': _highlightTranslated,
      'sourceLive': _sourceLive,
      'translatedLive': _translatedLive,
      'muted': service.muted,
      'volume': service.volume,
      'voiceId': service.voiceId,
      'voices': _voices,
      'cloning': _cloning,
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
    _activitySub?.cancel();
    _statusSub = null;
    _sourceSub = null;
    _translatedSub = null;
    _activitySub = null;
    if (_overlayShown) {
      try {
        await FlutterOverlayWindow.closeOverlay();
      } catch (_) {}
      _overlayShown = false;
    }
  }
}
