import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';
import 'package:url_launcher/url_launcher.dart';

import '../services/qonversion_service.dart';
// ignore: unused_import - used via _api.getMe() after purchase/restore
import '../services/api_client.dart';

/// Shown when user lacks premium entitlement or when API returns 402.
/// [onSuccess] is called when user gains premium (purchase or restore).
class PaywallScreen extends StatefulWidget {
  const PaywallScreen({
    super.key,
    this.onSuccess,
    this.showClose = true,
  });

  final VoidCallback? onSuccess;
  final bool showClose;

  @override
  State<PaywallScreen> createState() => _PaywallScreenState();
}

class _PaywallScreenState extends State<PaywallScreen> {
  final _api = ApiClient();
  List<PaywallProduct> _products = [];
  bool _loading = true;
  String? _error;
  bool _purchasing = false;
  bool _paypalConfigured = false;
  bool _paypalBusy = false;

  @override
  void initState() {
    super.initState();
    _loadOfferings();
    _checkPayPal();
  }

  Future<void> _checkPayPal() async {
    try {
      final cfg = await _api.getPayPalConfig();
      if (mounted) setState(() => _paypalConfigured = cfg['configured'] == true);
    } catch (_) {
      // leave disabled
    }
  }

  Future<String?> _currentEmail() async {
    try {
      final me = await _api.getMe();
      final email = me['email'] as String?;
      return (email != null && email.isNotEmpty) ? email : null;
    } catch (_) {
      return null;
    }
  }

  Future<void> _payWithPayPal(String tier, {required bool subscription}) async {
    if (_paypalBusy) return;
    setState(() { _paypalBusy = true; _error = null; });
    try {
      final email = await _currentEmail();
      if (email == null) {
        if (mounted) setState(() { _paypalBusy = false; _error = 'Please sign in first.'; });
        return;
      }
      String? approveUrl;
      if (subscription) {
        final res = await _api.createPayPalSubscription(email: email, tier: tier);
        approveUrl = res['approve_url'] as String?;
      } else {
        final res = await _api.createPayPalOrder(email: email, tier: tier);
        final links = (res['links'] as List?) ?? [];
        for (final l in links) {
          if (l is Map && (l['rel'] == 'approve' || l['rel'] == 'payer-action')) {
            approveUrl = l['href'] as String?;
            break;
          }
        }
      }
      if (approveUrl != null && approveUrl.isNotEmpty) {
        final ok = await launchUrl(Uri.parse(approveUrl), mode: LaunchMode.externalApplication);
        if (!ok && mounted) setState(() => _error = 'Could not open PayPal.');
      } else if (mounted) {
        setState(() => _error = 'PayPal checkout is unavailable right now.');
      }
    } catch (e) {
      if (mounted) setState(() => _error = 'PayPal checkout failed. Please try again.');
    } finally {
      if (mounted) setState(() => _paypalBusy = false);
    }
  }

  Future<void> _loadOfferings() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final offerings = await QonversionService.getOfferings();
      final products = offerings?.products ?? [];
      if (mounted) {
        setState(() {
          _products = products;
          _loading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        if (kDebugMode) debugPrint('Paywall loadOfferings error: $e');
        setState(() {
          _products = [];
          _loading = false;
          _error = 'Unable to load products. Please try again.';
        });
      }
    }
  }

  Future<void> _purchase(PaywallProduct product) async {
    if (_purchasing) return;
    setState(() {
      _purchasing = true;
      _error = null;
    });
    try {
      final success = await QonversionService.purchase(product);
      if (mounted) {
        setState(() => _purchasing = false);
        if (success) {
          await Future<void>.delayed(const Duration(milliseconds: 1500));
          try {
            if (mounted) await _api.getMe();
          } catch (_) {}
          if (mounted) widget.onSuccess?.call();
        } else {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Purchase was cancelled or failed. Please try again.')),
          );
        }
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _purchasing = false;
          _error = e.toString();
        });
      }
    }
  }

  Future<void> _restore() async {
    if (_purchasing) return;
    setState(() {
      _purchasing = true;
      _error = null;
    });
    try {
      final success = await QonversionService.restorePurchases();
      if (mounted) {
        setState(() => _purchasing = false);
        if (success) {
          await Future<void>.delayed(const Duration(milliseconds: 1500));
          try {
            if (mounted) await _api.getMe();
          } catch (_) {}
          if (mounted) widget.onSuccess?.call();
        } else {
          setState(() => _error = 'No active subscription found.');
        }
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _purchasing = false;
          _error = e.toString();
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: widget.showClose
          ? AppBar(
              title: const Text('Upgrade'),
              leading: IconButton(
                icon: const Icon(Icons.close),
                onPressed: () => Navigator.of(context).maybePop(),
              ),
            )
          : null,
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SizedBox(height: 16),
              Text(
                'Upgrade to continue',
                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                      fontWeight: FontWeight.bold,
                    ),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 8),
              Text(
                'Get more translation time and features with a subscription.',
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                      color: Theme.of(context).colorScheme.onSurfaceVariant,
                    ),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 24),
              if (_error != null) ...[
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.errorContainer,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Text(
                    _error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.onErrorContainer,
                    ),
                  ),
                ),
                const SizedBox(height: 16),
              ],
              if (_loading)
                const Center(child: Padding(padding: EdgeInsets.all(24), child: CircularProgressIndicator()))
              else ...[
                ..._products.map(
                  (p) => Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: FilledButton(
                      onPressed: _purchasing ? null : () => _purchase(p),
                      child: Padding(
                        padding: const EdgeInsets.symmetric(vertical: 12),
                        child: Text((p.prettyPrice?.isNotEmpty ?? false) ? '${p.id} — ${p.prettyPrice}' : p.id),
                      ),
                    ),
                  ),
                ),
                if (_products.isEmpty && !_loading)
                  Text(
                    'No plans available. Please try again later.',
                    style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                          color: Theme.of(context).colorScheme.onSurfaceVariant,
                        ),
                    textAlign: TextAlign.center,
                  ),
                const SizedBox(height: 16),
                TextButton(
                  onPressed: _purchasing ? null : _restore,
                  child: const Text('Restore purchases'),
                ),
                if (_paypalConfigured) ...[
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      const Expanded(child: Divider()),
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 8),
                        child: Text('or pay on the web',
                            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                                )),
                      ),
                      const Expanded(child: Divider()),
                    ],
                  ),
                  const SizedBox(height: 8),
                  OutlinedButton.icon(
                    onPressed: _paypalBusy ? null : () => _payWithPayPal('starter', subscription: true),
                    icon: const Icon(Icons.account_balance_wallet_outlined),
                    label: const Text('PayPal — Starter (monthly)'),
                  ),
                  const SizedBox(height: 8),
                  OutlinedButton.icon(
                    onPressed: _paypalBusy ? null : () => _payWithPayPal('pro', subscription: true),
                    icon: const Icon(Icons.account_balance_wallet_outlined),
                    label: const Text('PayPal — Pro (monthly)'),
                  ),
                  const SizedBox(height: 8),
                  OutlinedButton.icon(
                    onPressed: _paypalBusy ? null : () => _payWithPayPal('early_adopters', subscription: false),
                    icon: const Icon(Icons.workspace_premium_outlined),
                    label: const Text('PayPal — Early Adopters (lifetime)'),
                  ),
                  if (_paypalBusy)
                    const Padding(
                      padding: EdgeInsets.only(top: 12),
                      child: Center(child: CircularProgressIndicator()),
                    ),
                ],
              ],
            ],
          ),
        ),
      ),
    );
  }
}
