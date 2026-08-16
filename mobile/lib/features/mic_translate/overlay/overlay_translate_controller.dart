import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';

import '../mic_translate_service.dart';
import 'mic_foreground_service.dart';

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
  StreamSubscription<dynamic>? _overlayCmdSub;

  String _status = '';
  String _source = '';
  String _translated = '';
  bool _active = false;
  bool _overlayShown = false;

  bool get isActive => _active;
  bool get overlayShown => _overlayShown;

  /// Emits when translation starts/stops (including stop from the bubble).
  Stream<bool> get activeStream => _activeController.stream;

  static bool get _android =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.android;

  Future<OverlayStartResult> start() async {
    if (_active) {
      return OverlayStartResult(started: true, overlayShown: _overlayShown);
    }

    var overlayOk = false;
    if (_android) {
      try {
        final granted = await FlutterOverlayWindow.isPermissionGranted();
        if (granted) {
          overlayOk = true;
        } else {
          overlayOk = await FlutterOverlayWindow.requestPermission() ?? false;
        }
      } catch (_) {
        overlayOk = false;
      }
      await MicForegroundService.start();
    }

    final started = await service.start();
    if (!started) {
      if (_android) await MicForegroundService.stop();
      return const OverlayStartResult(started: false, overlayShown: false);
    }

    _active = true;
    _activeController.add(true);
    _bindServiceStreams();

    if (overlayOk) {
      try {
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
        _listenOverlayCommands();
        await _pushUpdate();
      } catch (_) {
        _overlayShown = false;
      }
    }

    return OverlayStartResult(started: true, overlayShown: _overlayShown);
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
    _overlayCmdSub?.cancel();
    _statusSub = null;
    _sourceSub = null;
    _translatedSub = null;
    _overlayCmdSub = null;
    if (!_activeController.isClosed) {
      _activeController.close();
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

  void _listenOverlayCommands() {
    _overlayCmdSub?.cancel();
    _overlayCmdSub = FlutterOverlayWindow.overlayListener.listen((event) {
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
      }
    });
  }

  Future<void> _pushUpdate() async {
    if (!_overlayShown) return;
    try {
      await FlutterOverlayWindow.shareData(
        jsonEncode({
          'type': 'update',
          'status': _status,
          'source': _source,
          'translated': _translated,
          'muted': service.muted,
        }),
      );
    } catch (_) {
      // Overlay may already be closed.
    }
  }

  Future<void> _tearDownOverlay() async {
    _statusSub?.cancel();
    _sourceSub?.cancel();
    _translatedSub?.cancel();
    _overlayCmdSub?.cancel();
    _statusSub = null;
    _sourceSub = null;
    _translatedSub = null;
    _overlayCmdSub = null;
    if (_overlayShown) {
      try {
        await FlutterOverlayWindow.closeOverlay();
      } catch (_) {}
      _overlayShown = false;
    }
  }
}
