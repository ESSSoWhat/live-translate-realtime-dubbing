import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../services/api_client.dart';
import 'paywall_screen.dart';

/// Manage Plan — shows the member's current tier + PayPal subscription status and
/// lets them cancel or switch/upgrade their plan.
class ManagePlanScreen extends StatefulWidget {
  const ManagePlanScreen({super.key});

  @override
  State<ManagePlanScreen> createState() => _ManagePlanScreenState();
}

class _ManagePlanScreenState extends State<ManagePlanScreen> {
  final _api = ApiClient();
  Map<String, dynamic>? _sub;
  bool _loading = true;
  bool _cancelling = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() { _loading = true; _error = null; });
    try {
      final s = await _api.getSubscription();
      if (mounted) setState(() { _sub = s; _loading = false; });
    } catch (_) {
      if (mounted) setState(() { _loading = false; _error = 'Could not load your plan'; });
    }
  }

  bool get _hasActiveSubscription =>
      (_sub?['subscription_id'] != null) &&
      ((_sub?['subscription_status'] as String?) ?? '') != 'canceled';

  Future<void> _confirmCancel() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Cancel subscription?'),
        content: const Text(
          "You'll keep your current plan until the end of the paid period, then move to Free.",
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Keep plan')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Cancel it')),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _cancelling = true);
    try {
      await _api.cancelSubscription();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Subscription cancelled. You keep access until the period ends.')),
      );
      await _load();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Could not cancel. Please try again.')),
        );
      }
    } finally {
      if (mounted) setState(() => _cancelling = false);
    }
  }

  void _openPaywall() {
    Navigator.of(context).push(MaterialPageRoute(builder: (_) => const PaywallScreen()));
  }

  Future<void> _switchToPro() async {
    setState(() => _cancelling = true);
    try {
      final res = await _api.reviseSubscription(tier: 'pro');
      final approveUrl = res['approve_url'] as String?;
      if (!mounted) return;
      if (approveUrl != null && approveUrl.isNotEmpty) {
        await launchUrl(Uri.parse(approveUrl), mode: LaunchMode.externalApplication);
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Approve the plan change in your browser to finish upgrading.')),
          );
        }
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Upgrade requested — your plan will update shortly.')),
        );
        await _load();
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Could not switch plans. Please try again.')),
        );
      }
    } finally {
      if (mounted) setState(() => _cancelling = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final tier = (_sub?['tier'] as String?) ?? 'free';
    final status = (_sub?['subscription_status'] as String?) ?? 'active';
    final nextBilling = _sub?['next_billing_time'] as String?;
    return Scaffold(
      appBar: AppBar(title: const Text('Manage plan')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(_error!),
                      const SizedBox(height: 12),
                      FilledButton(onPressed: _load, child: const Text('Retry')),
                    ],
                  ),
                )
              : ListView(
                  padding: const EdgeInsets.all(20),
                  children: [
                    Card(
                      child: Padding(
                        padding: const EdgeInsets.all(20),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text('Current plan',
                                style: Theme.of(context).textTheme.labelMedium?.copyWith(
                                      color: Theme.of(context).colorScheme.onSurfaceVariant,
                                    )),
                            const SizedBox(height: 4),
                            Text(tier.toUpperCase(),
                                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                                      fontWeight: FontWeight.bold,
                                    )),
                            const SizedBox(height: 8),
                            Text('Status: $status'),
                            if (nextBilling != null) ...[
                              const SizedBox(height: 4),
                              Text('Next billing: ${nextBilling.split('T').first}'),
                            ],
                          ],
                        ),
                      ),
                    ),
                    const SizedBox(height: 20),
                    if (tier == 'starter' && _hasActiveSubscription) ...[
                      FilledButton.icon(
                        onPressed: _cancelling ? null : _switchToPro,
                        icon: const Icon(Icons.upgrade),
                        label: const Text('Upgrade to Pro'),
                      ),
                      const SizedBox(height: 8),
                      TextButton(onPressed: _openPaywall, child: const Text('See all plans')),
                    ] else
                      FilledButton.icon(
                        onPressed: _openPaywall,
                        icon: const Icon(Icons.upgrade),
                        label: Text(tier == 'free' ? 'Choose a plan' : 'Change or upgrade plan'),
                      ),
                    if (_hasActiveSubscription) ...[
                      const SizedBox(height: 12),
                      OutlinedButton.icon(
                        onPressed: _cancelling ? null : _confirmCancel,
                        icon: _cancelling
                            ? const SizedBox(
                                width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                            : const Icon(Icons.cancel_outlined),
                        label: const Text('Cancel subscription'),
                        style: OutlinedButton.styleFrom(
                          foregroundColor: Theme.of(context).colorScheme.error,
                        ),
                      ),
                    ],
                  ],
                ),
    );
  }
}
