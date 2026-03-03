import 'package:dio/dio.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../config/api_config.dart';

const _accessTokenKey = 'access_token';
const _refreshTokenKey = 'refresh_token';

class AuthService {
  static const _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

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
    if (access != null) await _storage.write(key: _accessTokenKey, value: access);
    if (refresh != null) await _storage.write(key: _refreshTokenKey, value: refresh);
  }

  Future<void> clear() async {
    await _storage.delete(key: _accessTokenKey);
    await _storage.delete(key: _refreshTokenKey);
  }

  /// Returns true if a new access token was obtained.
  Future<bool> refreshIfNeeded() async {
    final refresh = await _storage.read(key: _refreshTokenKey);
    if (refresh == null) return false;
    try {
      final dio = Dio(BaseOptions(baseUrl: '${ApiConfig.baseUrl}api/v1'));
      final r = await dio.post(
        '/auth/refresh',
        data: {'refresh_token': refresh},
      );
      final data = r.data as Map<String, dynamic>?;
      if (data != null) {
        await saveFromAuthResponse(data);
        return true;
      }
    } catch (_) {
      await clear();
    }
    return false;
  }
}
