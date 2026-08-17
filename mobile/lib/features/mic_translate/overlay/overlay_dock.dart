import 'dart:ui' show PlatformDispatcher;

import 'package:flutter_overlay_window/flutter_overlay_window.dart';

/// Snap the floating bubble to the left or right screen border.
Future<void> dockOverlayToEdge({
  required int width,
  required int height,
  bool? preferRight,
}) async {
  try {
    final views = PlatformDispatcher.instance.views;
    if (views.isEmpty) return;
    final view = views.first;
    final screenW = view.physicalSize.width / view.devicePixelRatio;
    final screenH = view.physicalSize.height / view.devicePixelRatio;
    if (screenW <= 0 || screenH <= 0) return;

    var right = preferRight ?? true;
    var y = (screenH / 2) - (height / 2);
    try {
      final pos = await FlutterOverlayWindow.getOverlayPosition();
      if (preferRight == null) {
        right = pos.x + width / 2 >= screenW / 2;
      }
      y = pos.y;
    } catch (_) {}

    final maxX = (screenW - width).clamp(0, screenW);
    final maxY = (screenH - height).clamp(0, screenH);
    final x = right ? maxX.toDouble() : 0.0;
    y = y.clamp(0.0, maxY.toDouble());
    await FlutterOverlayWindow.moveOverlay(OverlayPosition(x, y));
  } catch (_) {}
}
