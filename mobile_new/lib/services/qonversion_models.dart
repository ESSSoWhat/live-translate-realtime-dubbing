/// Platform-agnostic paywall product (replaces QProduct in UI).
class PaywallProduct {
  const PaywallProduct({
    required this.id,
    this.prettyPrice,
    this.native,
  });

  final String id;
  final String? prettyPrice;
  final Object? native;
}

/// Platform-agnostic offerings (replaces QOfferings in UI).
class PaywallOfferings {
  const PaywallOfferings({required this.products});
  final List<PaywallProduct> products;
}
