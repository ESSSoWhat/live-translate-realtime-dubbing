import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:crypto/crypto.dart';
import 'package:dio/dio.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:sign_in_with_apple/sign_in_with_apple.dart';
import 'package:url_launcher/url_launcher.dart';

import '../config/api_config.dart';
import 'api_client.dart';
import 'auth_service.dart';
import 'qonversion_service.dart';

class SsoService {
  final _api = ApiClient();
  final _auth = AuthService();

  GoogleSignIn get _googleSignIn {
    final serverClientId = ApiConfig.googleWebClientId;
    if (serverClientId != null && serverClientId.isNotEmpty) {
      return GoogleSignIn(scopes: ['email', 'openid'], serverClientId: serverClientId);
    }
    return GoogleSignIn(scopes: ['email', 'openid']);
  }

  String _generateNonce([int length = 32]) {
    const charset =
        '0123456789ABCDEFGHIJKLMNOPQRSTUVXYZabcdefghijklmnopqrstuvwxyz-._';
    final random = Random.secure();
    return List.generate(length, (_) => charset[random.nextInt(charset.length)])
        .join();
  }

  String _sha256ofString(String input) {
    final bytes = utf8.encode(input);
    final digest = sha256.convert(bytes);
    return digest.toString();
  }

  Future<Map<String, dynamic>> signInWithGoogle() async {
    if (Platform.isAndroid &&
        (ApiConfig.googleWebClientId == null || ApiConfig.googleWebClientId!.isEmpty)) {
      throw SsoException(
        'Google Sign-In is not configured. Set GOOGLE_WEB_CLIENT_ID to your Web client ID '
        'from Google Cloud, and add your app SHA-1/SHA-256 to the OAuth client.',
      );
    }
    final account = await _googleSignIn.signIn();
    if (account == null) {
      throw SsoException('Google sign-in cancelled', cancelled: true);
    }
    final googleAuth = await account.authentication;
    final idToken = googleAuth.idToken;
    if (idToken == null) {
      throw SsoException('Failed to get Google ID token');
    }
    try {
      final body = await _api.loginWithGoogleIdToken(idToken);
      await _auth.saveFromAuthResponse(body);
      if (QonversionService.isAvailable) {
        final userId = body['user_id'] as String?;
        if (userId != null) await QonversionService.identify(userId);
      }
      return body;
    } catch (e) {
      throw _wrapSsoError(e, 'Google');
    }
  }

  /// Google sign-in via system browser (Windows/macOS/Linux). Call only when [Platform.isWindows] or [Platform.isMacOS] or [Platform.isLinux].
  Future<Map<String, dynamic>> signInWithGoogleDesktop() async {
    HttpServer? server;
    try {
      server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final port = server.port;
      final redirectUri = 'http://localhost:$port/';
      final authUrl = await _api.getGoogleOAuthUrl(redirectUri);

      final codeCompleter = Completer<String?>();
      server.listen((request) async {
        if (codeCompleter.isCompleted) return;
        final uri = request.uri;
        final code = uri.queryParameters['code'];
        request.response
          ..statusCode = 200
          ..headers.contentType = ContentType.html
          ..write(
            '<!DOCTYPE html><html><body><p>Sign-in complete. You can close this window.</p></body></html>',
          );
        await request.response.close();
        if (!codeCompleter.isCompleted) codeCompleter.complete(code);
      });

      if (!await launchUrl(Uri.parse(authUrl), mode: LaunchMode.externalApplication)) {
        throw SsoException('Could not open browser for Google sign-in.');
      }
      final code = await codeCompleter.future.timeout(
        const Duration(minutes: 5),
        onTimeout: () => null,
      );
      if (code == null || code.isEmpty) {
        throw SsoException('Google sign-in was cancelled or timed out.', cancelled: true);
      }
      final body = await _api.exchangeGoogleCode(code: code, redirectUri: redirectUri);
      await _auth.saveFromAuthResponse(body);
      if (QonversionService.isAvailable) {
        final userId = body['user_id'] as String?;
        if (userId != null) await QonversionService.identify(userId);
      }
      return body;
    } catch (e) {
      if (e is SsoException) rethrow;
      throw _wrapSsoError(e, 'Google');
    } finally {
      await server?.close(force: true);
    }
  }

  Future<Map<String, dynamic>> signInWithApple() async {
    final rawNonce = _generateNonce();
    final nonce = _sha256ofString(rawNonce);
    final credential = await SignInWithApple.getAppleIDCredential(
      scopes: [
        AppleIDAuthorizationScopes.email,
        AppleIDAuthorizationScopes.fullName,
      ],
      nonce: nonce,
    );
    final idToken = credential.identityToken;
    if (idToken == null) {
      throw SsoException('Failed to get Apple ID token');
    }
    try {
      final body = await _api.loginWithAppleIdToken(idToken, nonce: rawNonce);
      await _auth.saveFromAuthResponse(body);
      if (QonversionService.isAvailable) {
        final userId = body['user_id'] as String?;
        if (userId != null) await QonversionService.identify(userId);
      }
      return body;
    } catch (e) {
      throw _wrapSsoError(e, 'Apple');
    }
  }

  static Exception _wrapSsoError(Object e, String provider) {
    if (e is SsoException) return e;
    if (e is DioException) {
      final detail = e.response?.data;
      final String? message = detail is Map && detail.containsKey('detail')
          ? detail['detail']?.toString()
          : detail?.toString();
      if (message != null && message.isNotEmpty) {
        return SsoException(message);
      }
      if (e.type == DioExceptionType.connectionError ||
          e.type == DioExceptionType.connectionTimeout) {
        return SsoException('Network error. Check your connection.');
      }
    }
    return SsoException('$provider sign-in failed. Please try again.');
  }
}

/// SSO error with optional [cancelled] for user dismissal.
class SsoException implements Exception {
  SsoException(this.message, {this.cancelled = false});
  final String message;
  final bool cancelled;
  @override
  String toString() => message;
}
