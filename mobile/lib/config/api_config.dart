/// Backend base URL and Qonversion project key. Initialize with [ApiConfig.init] before use.
library;
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart' show kReleaseMode;

class ApiConfig {
  ApiConfig._();

  /// Web OAuth client ID (Google Cloud → Credentials → Web application).
  static const String defaultGoogleWebClientId =
      '683320997088-nbqaieaucd9rnpgn1ha2fvsjhqqh47m0.apps.googleusercontent.com';

  static String? _baseUrl;
  static String? _qonversionProjectKey;
  static String? _googleWebClientId;
  static String? _agoraAppId;

  static Future<void> init() async {
    const envUrl = String.fromEnvironment('API_BASE_URL', defaultValue: '');
    final String u;
    if (envUrl.isNotEmpty) {
      u = envUrl;
    } else if (kReleaseMode || Platform.isAndroid) {
      // Android (debug + release) and all release builds hit production by default.
      // Google Sign-In otherwise hangs on a dead localhost after the account picker.
      // Local backend: --dart-define=API_BASE_URL=http://10.0.2.2:8000/ (emulator)
      // or http://127.0.0.1:8000/ (desktop/iOS simulator).
      u = 'https://livetranslatedubtool-production.up.railway.app';
    } else {
      u = 'http://127.0.0.1:8000';
    }
    _baseUrl = u.endsWith('/') ? u : '$u/';
    _qonversionProjectKey = const String.fromEnvironment(
      'QONVERSION_PROJECT_KEY',
      defaultValue: '',
    );
    const webId = String.fromEnvironment(
      'GOOGLE_WEB_CLIENT_ID',
      defaultValue: defaultGoogleWebClientId,
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
    if (resolved.isEmpty) {
      resolved = defaultGoogleWebClientId;
    }
    _googleWebClientId = resolved;
    _agoraAppId = const String.fromEnvironment('AGORA_APP_ID', defaultValue: '');
  }

  static String? get agoraAppId =>
      _agoraAppId != null && _agoraAppId!.isNotEmpty ? _agoraAppId : null;

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

  /// Web client ID for Google Sign-In (required on Android for ID token verification).
  static String get googleWebClientId {
    final id = _googleWebClientId;
    if (id != null && id.isNotEmpty) return id;
    return defaultGoogleWebClientId;
  }
}