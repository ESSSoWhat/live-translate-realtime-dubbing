import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

/// Android: the phone's own number via Google's Phone Number Hint picker (no
/// phone permissions) and the SMS code via SMS User Consent. Elsewhere, or when
/// unavailable/dismissed, every call returns null and the user types instead.
class PhoneNumberHint {
  PhoneNumberHint._();

  static const _channel =
      MethodChannel('app.livetranslate.live_translate_mobile/phone_number');

  static bool get isSupported =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.android;

  static Future<String?> _call(String method) async {
    if (!isSupported) return null;
    try {
      final value = await _channel.invokeMethod<String>(method);
      return (value == null || value.isEmpty) ? null : value;
    } on PlatformException {
      return null;
    } on MissingPluginException {
      return null;
    }
  }

  /// Shows the SIM number picker; returns the chosen number or null.
  static Future<String?> requestPhoneNumber() => _call('requestHint');

  /// Waits (up to ~5 min) for the verification text and returns its 6-digit code
  /// after the user taps Allow, or null.
  static Future<String?> listenForCode() => _call('listenForCode');

  /// Stops waiting for the text. Never throws (it runs from dispose()).
  static Future<void> cancelCodeListener() async {
    if (!isSupported) return;
    try {
      // `void`, not `String`: the host acknowledges with a non-string value.
      await _channel.invokeMethod<void>('cancelCode');
    } on PlatformException {
      // Nothing to cancel.
    } on MissingPluginException {
      // No Android host.
    }
  }

  /// Start SMS User Consent, then run [request].
  ///
  /// Listening starts before [request] so a fast SMS is not missed. If the
  /// screen is gone when [request] finishes, the listener is cancelled and
  /// this returns null. [listenForCode] is returned so the caller can fill the
  /// code when it arrives. The typed-code path remains the fallback.
  static Future<({T? result, Future<String?> code})> requestWhileListening<T>({
    required bool Function() isMounted,
    required Future<T> Function() request,
  }) async {
    final code = (isSupported && isMounted())
        ? listenForCode()
        : Future<String?>.value(null);
    try {
      final result = await request();
      if (!isMounted()) {
        await cancelCodeListener();
        return (result: null, code: Future<String?>.value(null));
      }
      return (result: result, code: code);
    } catch (e) {
      await cancelCodeListener();
      rethrow;
    }
  }

  /// `+61 412-345 678` → `+61412345678`; null when not international format.
  static String? toE164(String raw) {
    final cleaned = raw.replaceAll(RegExp(r'[\s().-]'), '');
    final e164 = cleaned.startsWith('00') ? '+${cleaned.substring(2)}' : cleaned;
    return RegExp(r'^\+[1-9]\d{7,14}$').hasMatch(e164) ? e164 : null;
  }
}
