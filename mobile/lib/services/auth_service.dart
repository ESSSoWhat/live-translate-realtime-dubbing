import 'package:dio/dio.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../config/api_config.dart';

const _accessTokenKey = 'access_token';
const _refreshTokenKey = 'refresh_token';
const _userIdKey = 'user_id';

class AuthService {
  static const _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  static Future<bool>? _refreshFuture;

  Future<bool> hasTokens() async {
    final access = await _storage.read(key: _accessTokenKey);
    return access != null && access.isNotEmpty;
  }

  Future<String?> accessToken() async {
    return _storage.read(key: _accessTokenKey);
  }

  Future<String?> refreshToken() async {
    return _storage.read(key: _refreshTokenKey);
  }

  Future<void> saveFromAuthResponse(Map<String, dynamic> body) async {
    final access = body['access_token'] as String?;
    final refresh = body['refresh_token'] as String?;
    final userId = body['user_id'] as String?;
    if (access != null && refresh != null) {
      await _storage.write(key: _accessTokenKey, value: access);
      await _storage.write(key: _refreshTokenKey, value: refresh);
      if (userId != null) await _storage.write(key: _userIdKey, value: userId);
    } else if (access != null) {
      await _storage.write(key: _accessTokenKey, value: access);
      await _storage.delete(key: _refreshTokenKey);
      if (userId != null) await _storage.write(key: _userIdKey, value: userId);
    } else if (refresh != null) {
      await _storage.delete(key: _accessTokenKey);
      await _storage.write(key: _refreshTokenKey, value: refresh);
      if (userId != null) await _storage.write(key: _userIdKey, value: userId);
    }
  }

  Future<void> clear() async {
    await _storage.delete(key: _accessTokenKey);
    await _storage.delete(key: _refreshTokenKey);
    await _storage.delete(key: _userIdKey);
  }

  /// Backend user id (for Qonversion custom user id). Null if not logged in.
  Future<String?> userId() async => _storage.read(key: _userIdKey);

  /// Returns true if a new access token was obtained.
  Future<bool> refreshIfNeeded() async {
    if (_refreshFuture != null) {
      final ok = await _refreshFuture!;
      return ok;
    }
    final refresh = await _storage.read(key: _refreshTokenKey);
    if (refresh == null) return false;
    _refreshFuture = _doRefresh(refresh);
    try {
      final ok = await _refreshFuture!;
      return ok;
    } finally {
      _refreshFuture = null;
    }
  }

  Future<bool> _doRefresh(String refresh) async {
    try {
      final dio = Dio(BaseOptions(
        baseUrl: "${ApiConfig.baseUrl.endsWith('/') ? ApiConfig.baseUrl : '${ApiConfig.baseUrl}/'}api/v1",
        connectTimeout: const Duration(seconds: 10),
        receiveTimeout: const Duration(seconds: 10),
        validateStatus: (int? status) => status != null && status < 400,
      ));
      final r = await dio.post(
        '/auth/refresh',
        data: {'refresh_token': refresh},
      );
      final data = r.data;
      if (data is Map<String, dynamic>) {
        final access = data['access_token'] as String?;
        final newRefresh = data['refresh_token'] as String?;
        if (access != null) {
          await _storage.write(key: _accessTokenKey, value: access);
          if (newRefresh != null) {
            await _storage.write(key: _refreshTokenKey, value: newRefresh);
          }
          return true;
        }
      }
      return false;
    } on DioException catch (e) {
      final code = e.response?.statusCode;
      if (code == 401 || code == 403) {
        await clear();
      }
      rethrow;
    }
  }
}
