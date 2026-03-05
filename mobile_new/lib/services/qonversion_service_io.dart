import 'dart:async';
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
import 'package:qonversion_flutter/qonversion_flutter.dart';

import 'package:live_translate_mobile/config/api_config.dart';
import 'package:live_translate_mobile/services/qonversion_models.dart';

const String _kPremiumEntitlementId = 'premium';

/// Real Qonversion implementation for Android and iOS.
class QonversionService {
  QonversionService._();

  static bool _sdkInitialized = false;
  static Future<void>? _initFuture;

  static bool get _isMobile =>
      Platform.isAndroid || Platform.isIOS;

  static Future<void> init() async {
    if (_initFuture != null) {
      await _initFuture;
      return;
    }
    final completer = Completer<void>();
    _initFuture = completer.future;
    if (!_isMobile) {
      completer.complete();
      return;
    }
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
      _isMobile &&
      ApiConfig.qonversionProjectKey != null &&
      ApiConfig.qonversionProjectKey!.isNotEmpty;

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

  static Future<bool> checkEntitlements() async {
    if (!_sdkInitialized) return false;
    try {
      final entitlements =
          await Qonversion.getSharedInstance().checkEntitlements();
      final premium = entitlements[_kPremiumEntitlementId];
      return premium?.isActive ?? false;
    } catch (e) {
      if (kDebugMode) debugPrint('Qonversion checkEntitlements error: $e');
      return false;
    }
  }

  static Future<PaywallOfferings?> getOfferings() async {
    if (!_sdkInitialized) return null;
    try {
      final q = await Qonversion.getSharedInstance().offerings();
      if (q.main?.products == null) return null;
      final products = q.main!.products
          .map(
            (p) => PaywallProduct(
              id: p.qonversionId,
              prettyPrice: p.prettyPrice?.isNotEmpty == true ? p.prettyPrice : null,
              native: p,
            ),
          )
          .toList();
      return PaywallOfferings(products: products);
    } catch (e) {
      if (kDebugMode) debugPrint('Qonversion offerings error: $e');
      return null;
    }
  }

  static Future<bool> purchase(PaywallProduct product) async {
    if (!_sdkInitialized) return false;
    final q = product.native;
    if (q is! QProduct) return false;
    try {
      final result =
          await Qonversion.getSharedInstance().purchaseWithResult(q);
      if (!result.isSuccess) return false;
      final premium = result.entitlements?[_kPremiumEntitlementId];
      return premium?.isActive ?? false;
    } catch (e) {
      if (kDebugMode) debugPrint('Qonversion purchase error: $e');
      return false;
    }
  }

  static Future<bool> restorePurchases() async {
    if (!_sdkInitialized) return false;
    try {
      final entitlements = await Qonversion.getSharedInstance().restore();
      final premium = entitlements[_kPremiumEntitlementId];
      return premium?.isActive ?? false;
    } catch (e) {
      if (kDebugMode) debugPrint('Qonversion restore error: $e');
      return false;
    }
  }
}
