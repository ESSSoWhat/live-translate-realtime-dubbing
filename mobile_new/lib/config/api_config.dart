/// Backend base URL and Qonversion project key. Initialize with [ApiConfig.init] before use.
library;
import 'dart:io' show Platform;

class ApiConfig {
  ApiConfig._();

  static String? _baseUrl;
  static String? _qonversionProjectKey;

  static Future<void> init() async {
    const envUrl = String.fromEnvironment('API_BASE_URL', defaultValue: '');
    final String u;
    if (envUrl.isNotEmpty) {
      u = envUrl;
    } else {
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
  }

  static String get baseUrl {
    final u = _baseUrl;
    if (u == null) {
      throw StateError('ApiConfig not initialized. Call ApiConfig.init() before accessing baseUrl.');
    }
    return u;
  }

  static String? get qonversionProjectKey {
    if (_baseUrl == null) {
      throw StateError('ApiConfig not initialized. Call ApiConfig.init() before accessing qonversionProjectKey.');
    }
    final u = _qonversionProjectKey;
    return u != null && u.isNotEmpty ? u : null;
  }
}
