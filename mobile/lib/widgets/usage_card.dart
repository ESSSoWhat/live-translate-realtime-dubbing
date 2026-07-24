import 'package:flutter/material.dart';

import '../services/api_client.dart';

/// Live usage dashboard — shows tier and quota bars (used / limit) per feature
/// by polling GET /api/v1/user/usage. Compact card suitable for the home screen.
class UsageCard extends StatefulWidget {
  const UsageCard({super.key});

  @override
  State<UsageCard> createState() => _UsageCardState();
}

class _UsageCardState extends State<UsageCard> {
  final _api = ApiClient();
  Map<String, dynamic>? _usage;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (mounted) setState(() { _loading = true; _error = null; });
    try {
      final u = await _api.getUsage();
      if (mounted) setState(() { _usage = u; _loading = false; });
    } catch (_) {
      if (mounted) setState(() { _loading = false; _error = 'Could not load usage'; });
    }
  }

  int _int(String key) => (_usage?[key] as num?)?.toInt() ?? 0;

  Widget _bar(String label, int used, int limit, {bool isTime = false}) {
    final pct = limit > 0 ? (used / limit).clamp(0.0, 1.0) : 0.0;
    final near = pct >= 0.9;
    final usedStr = isTime ? '${(used / 60).floor()} min' : _fmt(used);
    final limitStr = limit >= 2147483647
        ? '∞'
        : (isTime ? '${(limit / 60).floor()} min' : _fmt(limit));
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(label, style: Theme.of(context).textTheme.bodySmall),
              Text('$usedStr / $limitStr',
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: near
                            ? Theme.of(context).colorScheme.error
                            : Theme.of(context).colorScheme.onSurfaceVariant,
                      )),
            ],
          ),
          const SizedBox(height: 4),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
              value: limit >= 2147483647 ? 0.02 : pct,
              minHeight: 8,
              backgroundColor: Theme.of(context).colorScheme.surfaceContainerHighest,
              color: near
                  ? Theme.of(context).colorScheme.error
                  : Theme.of(context).colorScheme.primary,
            ),
          ),
        ],
      ),
    );
  }

  static String _fmt(int n) {
    if (n >= 1000000) return '${(n / 1000000).toStringAsFixed(1)}M';
    if (n >= 1000) return '${(n / 1000).toStringAsFixed(1)}k';
    return '$n';
  }

  @override
  Widget build(BuildContext context) {
    final tier = (_usage?['tier'] as String?) ?? 'free';
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text('Your usage',
                    style: Theme.of(context).textTheme.titleSmall?.copyWith(
                          fontWeight: FontWeight.bold,
                        )),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.secondaryContainer,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Text(tier.toUpperCase(),
                      style: Theme.of(context).textTheme.labelSmall?.copyWith(
                            color: Theme.of(context).colorScheme.onSecondaryContainer,
                            fontWeight: FontWeight.bold,
                          )),
                ),
              ],
            ),
            const SizedBox(height: 12),
            if (_loading)
              const Padding(
                padding: EdgeInsets.all(8),
                child: Center(child: CircularProgressIndicator()),
              )
            else if (_error != null)
              Row(
                children: [
                  Expanded(child: Text(_error!, style: Theme.of(context).textTheme.bodySmall)),
                  TextButton(onPressed: _load, child: const Text('Retry')),
                ],
              )
            else ...[
              _bar('Dubbing', _int('dubbing_seconds_used'), _int('dubbing_seconds_limit'), isTime: true),
              _bar('Text-to-speech', _int('tts_chars_used'), _int('tts_chars_limit')),
              _bar('Translation', _int('translation_chars_used'), _int('translation_chars_limit')),
            ],
          ],
        ),
      ),
    );
  }
}
