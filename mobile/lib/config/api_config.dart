/// Backend base URL and Qonversion project key. Initialize with [ApiConfig.init] before use.
library;
import 'dart:io' show Platform;

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
    } else {
      // Local FastAPI (`cd backend && uvicorn app.main:app --reload` on port 8000).
      // Do not default to `api.livetranslate.app` — it has no public DNS as of now.
      // Android emulator → host machine via 10.0.2.2; iOS simulator & desktop → 127.0.0.1.
      // Physical phones & production store builds: pass `--dart-define=API_BASE_URL=https://.../`.
      if (Platform.isAndroid) {
        u = 'http://10.0.2.2:8000';
      } else {
        u = 'http://127.0.0.1:8000';
      }
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
