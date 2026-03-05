import 'dart:convert';
import 'dart:math';

import 'package:crypto/crypto.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:sign_in_with_apple/sign_in_with_apple.dart';

import 'api_client.dart';
import 'auth_service.dart';
import 'qonversion_service.dart';

class SsoService {
  final _api = ApiClient();
  final _auth = AuthService();

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
    final result = await GoogleSignIn.instance.authenticate();
    if (result == null) {
      throw Exception('Google sign-in cancelled');
    }
    final idToken = result.credential?.idToken;
    if (idToken == null) {
      throw Exception('Failed to get Google ID token');
    }
    final body = await _api.loginWithGoogleIdToken(idToken);
    await _auth.saveFromAuthResponse(body);
    if (QonversionService.isAvailable) {
      final userId = body['user_id'] as String?;
      if (userId != null) await QonversionService.identify(userId);
    }
    return body;
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
      throw Exception('Failed to get Apple ID token');
    }
    final body = await _api.loginWithAppleIdToken(idToken, nonce: rawNonce);
    await _auth.saveFromAuthResponse(body);
    if (QonversionService.isAvailable) {
      final userId = body['user_id'] as String?;
      if (userId != null) await QonversionService.identify(userId);
    }
    return body;
  }
}
