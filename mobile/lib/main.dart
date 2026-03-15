import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:live_translate_mobile/app.dart';
import 'package:live_translate_mobile/config/api_config.dart';
import 'package:live_translate_mobile/firebase_options.dart';
import 'package:live_translate_mobile/services/qonversion_service.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await Firebase.initializeApp(
    options: DefaultFirebaseOptions.currentPlatform,
  );
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
