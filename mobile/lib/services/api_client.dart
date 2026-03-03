import 'package:dio/dio.dart';

import '../config/api_config.dart';
import 'auth_service.dart';

/// HTTP client for Live Translate API with auth interceptor. for Live Translate API with auth interceptor.
class ApiClient {
  ApiClient() {
    _dio.options.baseUrl = '${ApiConfig.baseUrl}api/v1';
    _dio.options.connectTimeout = const Duration(seconds: 30);
    _dio.options.receiveTimeout = const Duration(seconds: 60);
    _dio.interceptors.add(InterceptorsWrapper(
      onRequest: (options, handler) async {
        final token = await AuthService().accessToken();
        if (token != null) {
          options.headers['Authorization'] = 'Bearer $token';
        }
        return handler.next(options);
      },
      onError: (err, handler) async {
        if (err.response?.statusCode == 401) {
          final refreshed = await AuthService().refreshIfNeeded();
          if (refreshed) {
            final token = await AuthService().accessToken();
            if (token != null) {
              err.requestOptions.headers['Authorization'] = 'Bearer $token';
              return handler.resolve(
                await _dio.fetch(err.requestOptions),
              );
            }
          }
        }
        return handler.next(err);
      },
    ));
  }

  final Dio _dio = Dio();

  /// POST /auth/login
  Future<Map<String, dynamic>> login(String email, String password) async {
    final r = await _dio.post(
      '/auth/login',
      data: {'email': email, 'password': password},
    );
    return r.data as Map<String, dynamic>;
  }

  /// POST /auth/register
  Future<Map<String, dynamic>> register(String email, String password) async {
    final r = await _dio.post(
      '/auth/register',
      data: {'email': email, 'password': password},
    );
    return r.data as Map<String, dynamic>;
  }

  /// POST /proxy/transcribe — multipart audio, returns { text, language_code }.
  Future<Map<String, dynamic>> transcribe(
    List<int> audioBytes, {
    String language = 'auto',
  }) async {
    final formData = FormData.fromMap({
      'audio': MultipartFile.fromBytes(
        audioBytes,
        filename: 'audio.raw',
      ),
      'language': language,
    });
    final r = await _dio.post('/proxy/transcribe', data: formData);
    return r.data as Map<String, dynamic>;
  }

  /// POST /proxy/translate — returns { translated_text, source_language }.
  Future<Map<String, dynamic>> translate({
    required String text,
    required String targetLanguage,
    String sourceLanguage = 'auto',
  }) async {
    final r = await _dio.post(
      '/proxy/translate',
      data: {
        'text': text,
        'target_language': targetLanguage,
        'source_language': sourceLanguage,
      },
    );
    return r.data as Map<String, dynamic>;
  }

  /// POST /proxy/synthesize — returns audio bytes (stream or body).
  Future<List<int>> synthesize({
    required String text,
    required String voiceId,
    String modelId = 'eleven_flash_v2_5',
  }) async {
    final r = await _dio.post<List<int>>(
      '/proxy/synthesize',
      data: {
        'text': text,
        'voice_id': voiceId,
        'model_id': modelId,
      },
      options: Options(responseType: ResponseType.bytes),
    );
    return r.data ?? [];
  }

  /// GET /proxy/voices — returns list of { voice_id, name, category }.
  Future<List<Map<String, dynamic>>> getVoices() async {
    final r = await _dio.get('/proxy/voices');
    final list = r.data as List<dynamic>? ?? [];
    return list.map((e) => Map<String, dynamic>.from(e as Map)).toList();
  }
}
