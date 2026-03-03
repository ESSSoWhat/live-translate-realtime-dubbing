/// Backend base URL and config. Set via --dart-define or default.
class ApiConfig {
  static late String baseUrl;

  static Future<void> init() async {
    baseUrl = const String.fromEnvironment(
      'API_BASE_URL',
      defaultValue: 'https://api.livetranslate.app',
    );
    if (!baseUrl.endsWith('/')) {
      baseUrl = '$baseUrl/';
    }
  }
}
