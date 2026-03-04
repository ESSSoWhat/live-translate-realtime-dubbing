/// Backend base URL and Qonversion project key. Initialize with [ApiConfig.init] before use.
class ApiConfig {
  ApiConfig._();

  static String? _baseUrl;
  static String? _qonversionProjectKey;

  static Future<void> init() async {
    final u = const String.fromEnvironment(
      'API_BASE_URL',
      defaultValue: 'https://api.livetranslate.app',
    );
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

  static String? get qonversionProjectKey =>
      _qonversionProjectKey != null && _qonversionProjectKey!.isNotEmpty
          ? _qonversionProjectKey
          : null;
}
