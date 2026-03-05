import 'package:live_translate_mobile/services/qonversion_models.dart';

/// No-op implementation for web and desktop (Qonversion is mobile-only).
class QonversionService {
  QonversionService._();

  static Future<void> init() async {}

  static bool get isAvailable => false;

  static Future<void> identify(String backendUserId) async {}

  static Future<bool> checkEntitlements() async => false;

  static Future<PaywallOfferings?> getOfferings() async => null;

  static Future<bool> purchase(PaywallProduct product) async => false;

  static Future<bool> restorePurchases() async => false;
}
