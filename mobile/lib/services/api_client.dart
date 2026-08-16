import 'package:dio/dio.dart';

import '../config/api_config.dart';
import 'auth_service.dart';

/// Thrown when the API returns 402 (quota exceeded). Show upgrade/paywall.
class QuotaExceededException implements Exception {
  QuotaExceededException([this.message]);
  final String? message;
  @override
  String toString() => message ?? 'Quota exceeded';
}

/// HTTP client for Live Translate API with auth interceptor.
class ApiClient {
  ApiClient() {
    final base = ApiConfig.baseUrl;
    _dio.options.baseUrl = base.endsWith('/') ? '${base}api/v1' : '$base/api/v1';
    _dio.options.connectTimeout = const Duration(seconds: 30);
    _dio.options.receiveTimeout = const Duration(seconds: 60);
    _dio.options.validateStatus = (int? status) => status != null && status < 400;
    _dio.interceptors.add(InterceptorsWrapper(
      onRequest: (options, handler) async {
        final token = await AuthService().accessToken();
        if (token != null) {
          options.headers['Authorization'] = 'Bearer $token';
        }
        return handler.next(options);
      },
      onError: (err, handler) async {
        const retriedKey = 'auth_retried';
        if (err.requestOptions.extra[retriedKey] == true) {
          return handler.next(err);
        }
        if (err.response?.statusCode == 401) {
          final refreshed = await AuthService().refreshIfNeeded();
          if (refreshed) {
            final token = await AuthService().accessToken();
            if (token != null) {
              err.requestOptions.extra[retriedKey] = true;
              err.requestOptions.headers['Authorization'] = 'Bearer $token';
              return handler.resolve(
                await _dio.fetch(err.requestOptions),
              );
            }
          }
        }
        if (err.response?.statusCode == 402) {
          final detail = err.response?.data;
          final String? message = detail is Map && detail.containsKey('detail')
              ? detail['detail']?.toString()
              : detail?.toString();
          return handler.reject(
            DioException(
              requestOptions: err.requestOptions,
              response: err.response,
              error: QuotaExceededException(message),
            ),
          );
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
    final data = r.data;
    if (data is Map<String, dynamic>) return data;
    throw DioException(
      requestOptions: r.requestOptions,
      error: 'Unexpected response format',
    );
  }

  /// POST /auth/register
  Future<Map<String, dynamic>> register(String email, String password) async {
    final r = await _dio.post(
      '/auth/register',
      data: {'email': email, 'password': password},
    );
    final data = r.data;
    if (data is Map<String, dynamic>) return data;
    throw DioException(
      requestOptions: r.requestOptions,
      error: 'Unexpected response format',
    );
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
    final data = r.data;
    if (data is Map<String, dynamic>) return data;
    throw DioException(
      requestOptions: r.requestOptions,
      error: 'Unexpected response format',
    );
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
    final data = r.data;
    if (data is Map<String, dynamic>) return data;
    throw DioException(
      requestOptions: r.requestOptions,
      error: 'Unexpected response format',
    );
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

  /// GET /user/me — returns user profile with tier, subscription_status, usage.
  Future<Map<String, dynamic>> getMe() async {
    final r = await _dio.get<Map<String, dynamic>>('/user/me');
    final data = r.data;
    if (data is Map<String, dynamic>) return data;
    throw DioException(
      requestOptions: r.requestOptions,
      error: 'Unexpected response format',
    );
  }

  /// GET /user/usage — returns tier + used/limit per feature for the current period.
  Future<Map<String, dynamic>> getUsage() async {
    final r = await _dio.get<Map<String, dynamic>>('/user/usage');
    final data = r.data;
    if (data is Map<String, dynamic>) return data;
    throw DioException(
      requestOptions: r.requestOptions,
      error: 'Unexpected response format',
    );
  }

  /// GET /paypal/config — public PayPal config ({configured, env, currency, ...}).
  Future<Map<String, dynamic>> getPayPalConfig() async {
    final r = await _dio.get<Map<String, dynamic>>('/paypal/config');
    final data = r.data;
    if (data is Map<String, dynamic>) return data;
    return {'configured': false};
  }

  /// POST /paypal/subscriptions — start a recurring subscription (starter|pro).
  /// Returns { subscription_id, status, approve_url } — open approve_url in a browser.
  Future<Map<String, dynamic>> createPayPalSubscription({
    required String email,
    required String tier,
  }) async {
    final r = await _dio.post(
      '/paypal/subscriptions',
      data: {'email': email, 'tier': tier},
    );
    final data = r.data;
    if (data is Map<String, dynamic>) return data;
    throw DioException(
      requestOptions: r.requestOptions,
      error: 'Unexpected response format',
    );
  }

  /// POST /paypal/orders — start a one-time order (e.g. early_adopters).
  /// Returns { order_id, status, links } — open the 'approve' link in a browser.
  Future<Map<String, dynamic>> createPayPalOrder({
    required String email,
    String tier = 'early_adopters',
  }) async {
    final r = await _dio.post(
      '/paypal/orders',
      data: {'email': email, 'tier': tier},
    );
    final data = r.data;
    if (data is Map<String, dynamic>) return data;
    throw DioException(
      requestOptions: r.requestOptions,
      error: 'Unexpected response format',
    );
  }

  /// POST /paypal/orders/{order_id}/capture — capture an approved one-time order.
  /// Call after the buyer approves in the browser; on COMPLETED the backend
  /// provisions the tier. Returns { status, order_id }.
  Future<Map<String, dynamic>> capturePayPalOrder(String orderId) async {
    final r = await _dio.post('/paypal/orders/$orderId/capture');
    final data = r.data;
    if (data is Map<String, dynamic>) return data;
    throw DioException(
      requestOptions: r.requestOptions,
      error: 'Unexpected response format',
    );
  }

  /// GET /paypal/subscription — current user's plan + live PayPal status.
  Future<Map<String, dynamic>> getSubscription() async {
    final r = await _dio.get<Map<String, dynamic>>('/paypal/subscription');
    final data = r.data;
    if (data is Map<String, dynamic>) return data;
    throw DioException(
      requestOptions: r.requestOptions,
      error: 'Unexpected response format',
    );
  }

  /// POST /paypal/subscription/cancel — cancel the current user's PayPal subscription.
  Future<Map<String, dynamic>> cancelSubscription() async {
    final r = await _dio.post('/paypal/subscription/cancel');
    final data = r.data;
    if (data is Map<String, dynamic>) return data;
    throw DioException(
      requestOptions: r.requestOptions,
      error: 'Unexpected response format',
    );
  }

  /// POST /paypal/subscription/revise — switch an existing subscription to another plan.
  /// Returns { approve_url } when PayPal needs buyer approval (price increase).
  Future<Map<String, dynamic>> reviseSubscription({required String tier}) async {
    final r = await _dio.post('/paypal/subscription/revise', data: {'tier': tier});
    final data = r.data;
    if (data is Map<String, dynamic>) return data;
    throw DioException(
      requestOptions: r.requestOptions,
      error: 'Unexpected response format',
    );
  }

  /// POST /analytics/nudge — record an upgrade-nudge A/B impression/click (best-effort).
  Future<void> recordNudge({
    required String variant,
    required String action,
    String? feature,
    String surface = 'mobile',
  }) async {
    try {
      await _dio.post('/analytics/nudge', data: {
        'variant': variant,
        'action': action,
        'feature': feature,
        'surface': surface,
      });
    } catch (_) {
      // analytics is best-effort; never block the UX
    }
  }

  /// POST /auth/oauth/google/id-token — login with Google ID token (Android/iOS).
  Future<Map<String, dynamic>> loginWithGoogleIdToken(
    String idToken, {
    String? nonce,
  }) async {
    final r = await _dio.post(
      '/auth/oauth/google/id-token',
      data: {'id_token': idToken, if (nonce != null) 'nonce': nonce},
    );
    final data = r.data;
    if (data is Map<String, dynamic>) return data;
    throw DioException(
      requestOptions: r.requestOptions,
      error: 'Unexpected response format',
    );
  }

  /// GET /auth/oauth/google — URL for desktop browser OAuth (Windows/macOS/Linux).
  Future<String> getGoogleOAuthUrl(String redirectUri) async {
    final r = await _dio.get<dynamic>(
      '/auth/oauth/google',
      queryParameters: {'redirect_uri': redirectUri},
    );
    final m = r.data;
    final url = m is Map ? m['url'] as String? : null;
    if (url != null && url.isNotEmpty) return url;
    throw DioException(
      requestOptions: r.requestOptions,
      error: 'Missing OAuth URL in response',
    );
  }

  /// POST /auth/oauth/google/exchange — exchange auth code for session (desktop).
  Future<Map<String, dynamic>> exchangeGoogleCode({
    required String code,
    required String redirectUri,
  }) async {
    final r = await _dio.post(
      '/auth/oauth/google/exchange',
      data: {'code': code, 'redirect_uri': redirectUri},
    );
    final data = r.data;
    if (data is Map<String, dynamic>) return data;
    throw DioException(
      requestOptions: r.requestOptions,
      error: 'Unexpected response format',
    );
  }

  /// POST /voice/translated-call — start a translated phone call (Twilio).
  Future<Map<String, dynamic>> startTranslatedCall({
    required String userPhone,
    required String destPhone,
    String targetLang = 'es',
  }) async {
    final r = await _dio.post(
      '/voice/translated-call',
      data: {
        'user_phone': userPhone,
        'dest_phone': destPhone,
        'target_lang': targetLang,
      },
    );
    final data = r.data;
    if (data is Map<String, dynamic>) return data;
    throw DioException(
      requestOptions: r.requestOptions,
      error: 'Unexpected response format',
    );
  }

  /// POST /auth/oauth/apple/id-token — login with Apple ID token.
  Future<Map<String, dynamic>> loginWithAppleIdToken(
    String idToken, {
    String? nonce,
  }) async {
    final r = await _dio.post(
      '/auth/oauth/apple/id-token',
      data: {'id_token': idToken, if (nonce != null) 'nonce': nonce},
    );
    final data = r.data;
    if (data is Map<String, dynamic>) return data;
    throw DioException(
      requestOptions: r.requestOptions,
      error: 'Unexpected response format',
    );
  }
}
