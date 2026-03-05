import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';

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

  @override
  void initState() {
    super.initState();
    _loadOfferings();
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
              ],
            ],
          ),
        ),
      ),
    );
  }
}
