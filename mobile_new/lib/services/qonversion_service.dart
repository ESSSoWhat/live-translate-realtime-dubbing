import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:qonversion_flutter/qonversion_flutter.dart';

import '../config/api_config.dart';

/// Entitlement id used to gate premium features (must match Qonversion dashboard).
const String kPremiumEntitlementId = 'premium';

/// Wrapper around Qonversion SDK: init, identify, entitlements, purchase, restore.
class QonversionService {
  QonversionService._();

  static bool _sdkInitialized = false;
  static Future<void>? _initFuture;

  static Future<void> init() async {
    if (_initFuture != null) {
      await _initFuture;
      return;
    }
    final completer = Completer<void>();
    _initFuture = completer.future;
    final projectKey = ApiConfig.qonversionProjectKey;
    if (projectKey == null || projectKey.isEmpty) {
      if (kDebugMode) {
        debugPrint('Qonversion: no project key; skipping init');
      }
      completer.complete();
      return;
    }
    try {
      final config = QonversionConfigBuilder(
        projectKey,
        QLaunchMode.subscriptionManagement,
      ).build();
      Qonversion.initialize(config);
      _sdkInitialized = true;
      completer.complete();
    } catch (e, s) {
      if (kDebugMode) {
        debugPrint('Qonversion init error: $e $s');
      }
      completer.completeError(e, s);
      _initFuture = null;
      rethrow;
    }
  }

  static bool get isAvailable =>
      ApiConfig.qonversionProjectKey != null &&
      ApiConfig.qonversionProjectKey!.isNotEmpty;

  /// Identify user with backend user id so webhooks map to same user.
  static Future<void> identify(String backendUserId) async {
    if (!_sdkInitialized || backendUserId.isEmpty) return;
    try {
      await Qonversion.getSharedInstance().setUserProperty(
        QUserPropertyKey.customUserId,
        backendUserId,
      );
    } catch (e) {
      if (kDebugMode) debugPrint('Qonversion identify error: $e');
    }
  }

  /// Returns true if user has active premium entitlement.
  static Future<bool> checkEntitlements() async {
    if (!_sdkInitialized) return false;
    try {
      final entitlements = await Qonversion.getSharedInstance().checkEntitlements();
      final premium = entitlements[kPremiumEntitlementId];
      return premium?.isActive ?? false;
    } catch (e) {
      if (kDebugMode) debugPrint('Qonversion checkEntitlements error: $e');
      return false;
    }
  }

  /// Available offerings (for paywall UI).
  static Future<QOfferings?> getOfferings() async {
    if (!_sdkInitialized) return null;
    try {
      return await Qonversion.getSharedInstance().offerings();
    } catch (e) {
      if (kDebugMode) debugPrint('Qonversion offerings error: $e');
      return null;
    }
  }

  /// Purchase a product by id. Returns true if purchase succeeded and user has entitlement.
  static Future<bool> purchase(QProduct product) async {
    if (!_sdkInitialized) return false;
    try {
      final result = await Qonversion.getSharedInstance().purchaseWithResult(product);
      if (!result.isSuccess) return false;
      final entitlements = result.entitlements;
      final premium = entitlements?[kPremiumEntitlementId];
      return premium?.isActive ?? false;
    } catch (e) {
      if (kDebugMode) debugPrint('Qonversion purchase error: $e');
      return false;
    }
  }

  /// Restore purchases. Returns true if user has premium after restore.
  static Future<bool> restorePurchases() async {
    if (!_sdkInitialized) return false;
    try {
      final entitlements = await Qonversion.getSharedInstance().restore();
      final premium = entitlements[kPremiumEntitlementId];
      return premium?.isActive ?? false;
    } catch (e) {
      if (kDebugMode) debugPrint('Qonversion restore error: $e');
      return false;
    }
  }
}
