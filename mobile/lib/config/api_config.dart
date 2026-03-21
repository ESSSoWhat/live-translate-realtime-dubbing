/// Backend base URL and Qonversion project key. Initialize with [ApiConfig.init] before use.
library;
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart' show kDebugMode;

class ApiConfig {
  ApiConfig._();

  static String? _baseUrl;
  static String? _qonversionProjectKey;
  static String? _googleWebClientId;

  static Future<void> init() async {
    const envUrl = String.fromEnvironment('API_BASE_URL', defaultValue: '');
    final String u;
    if (envUrl.isNotEmpty) {
      u = envUrl;
    } else if (kDebugMode) {
      // Local FastAPI backend (`cd backend && uvicorn ...`). Android emulator → host via 10.0.2.2.
      u = Platform.isAndroid ? 'http://10.0.2.2:8000' : 'http://127.0.0.1:8000';
    } else {
      // Production: set `--dart-define=API_BASE_URL=https://your-deployed-api/` — do not rely on a
      // placeholder host. `api.livetranslate.app` may be unset in DNS until you configure it.
      u = 'https://api.livetranslate.app';
    }
    _baseUrl = u.endsWith('/') ? u : '$u/';
    _qonversionProjectKey = const String.fromEnvironment(
      'QONVERSION_PROJECT_KEY',
      defaultValue: '',
    );
    final webId = const String.fromEnvironment(
      'GOOGLE_WEB_CLIENT_ID',
      defaultValue: '',
    );
    String resolved = webId;
    if (resolved.isEmpty) {
      try {
        final fromEnv = Platform.environment['GOOGLE_WEB_CLIENT_ID'];
        if (fromEnv != null && fromEnv.isNotEmpty) resolved = fromEnv;
      } catch (_) {
        // Platform not available (e.g. web)
      }
    }
    _googleWebClientId = resolved.isEmpty ? null : resolved;
  }

  static String get baseUrl {
    final u = _baseUrl;
    if (u == null) {
      throw StateError('ApiConfig not initialized. Call ApiConfig.init() before accessing baseUrl.');
    }
    return u;
  }

  static String? get qonversionProjectKey =>
      _qonversionProjectKey != null && _qonversionProjectKey!.isNotEmpty
          ? _qonversionProjectKey
          : null;

  /// Web client ID for Google Sign-In (required on Android for Supabase ID token verification).
  static String? get googleWebClientId => _googleWebClientId;
}
