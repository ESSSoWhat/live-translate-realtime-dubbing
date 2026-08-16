import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:live_translate_mobile/app.dart';
import 'package:live_translate_mobile/config/api_config.dart';
import 'package:live_translate_mobile/features/mic_translate/overlay/overlay_entry.dart';
import 'package:live_translate_mobile/services/qonversion_service.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await ApiConfig.init();
  await QonversionService.init();
  await SystemChrome.setPreferredOrientations([
    DeviceOrientation.portraitUp,
    DeviceOrientation.portraitDown,
    DeviceOrientation.landscapeLeft,
    DeviceOrientation.landscapeRight,
  ]);
  runApp(const LiveTranslateApp());
}

/// Android overlay bubble entry (flutter_overlay_window).
@pragma('vm:entry-point')
void overlayMain() => runOverlayTranslateApp();
