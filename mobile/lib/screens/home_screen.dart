import 'dart:async';

import 'package:flutter/material.dart';

import '../services/auth_service.dart';
import '../services/api_client.dart';
import 'login_screen.dart';
import 'paywall_screen.dart';
import 'settings_screen.dart';
import '../features/mic_translate/mic_translate_service.dart';
import '../features/translated_call/phone_call_screen.dart';
import '../features/translated_call/translated_call_screen.dart';
import '../services/qonversion_service.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final _auth = AuthService();
  final _api = ApiClient();
  final _translateService = MicTranslateService();
  bool _translating = false;
  String? _status;
  StreamSubscription<String>? _statusSub;
  StreamSubscription<void>? _paywallSub;

  @override
  void initState() {
    super.initState();
    _statusSub = _translateService.statusStream.listen((s) {
      if (mounted) setState(() => _status = s);
    });
    _paywallSub = _translateService.paywallRequiredStream.listen((_) {
      if (mounted) _showPaywall();
    });
  }

  @override
  void dispose() {
    _statusSub?.cancel();
    _paywallSub?.cancel();
    _translateService.dispose();
    super.dispose();
  }

  void _showPaywall() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => PaywallScreen(
          showClose: true,
          onSuccess: () {
            Navigator.of(context).maybePop();
          },
        ),
      ),
    );
  }

  bool _isToggling = false;

  /// True if user has premium access via Qonversion entitlements or backend tier.
  Future<bool> _hasPremiumAccess() async {
    if (QonversionService.isAvailable &&
        await QonversionService.checkEntitlements()) {
      return true;
    }
    try {
      final me = await _api.getMe();
      final tier = me['tier'] as String?;
      return tier != null && tier != 'free';
    } catch (_) {
      return false;
    }
  }

  Future<void> _toggleTranslate() async {
    if (_isToggling) return;
    _isToggling = true;
    try {
      if (_translating) {
        await _translateService.stop();
        if (mounted) {
          setState(() {
            _translating = false;
            _status = null;
          });
        }
        return;
      }
      if (!await _hasPremiumAccess()) {
        if (!mounted) return;
        _showPaywall();
        _isToggling = false;
        return;
      }
      final started = await _translateService.start();
      if (mounted) setState(() => _translating = started);
    } catch (e) {
      if (mounted) setState(() => _translating = false);
    } finally {
      if (mounted) _isToggling = false;
    }
  }

  Future<void> _logout() async {
    await _translateService.stop();
    await _auth.clear();
    if (!mounted) return;
    Navigator.of(context).pushAndRemoveUntil(
      MaterialPageRoute(builder: (_) => const LoginScreen()),
      (_) => false,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Live Translate'),
        actions: [
          IconButton(
            icon: const Icon(Icons.settings),
            onPressed: () {
              Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const SettingsScreen()),
              );
            },
          ),
        ],
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SizedBox(height: 24),
              Text(
                'Mic translation plays through your device speaker.',
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                      color: Theme.of(context).colorScheme.onSurfaceVariant,
                    ),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 32),
              Expanded(
                child: Center(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(
                        _translating ? Icons.mic : Icons.mic_none,
                        size: 80,
                        color: _translating
                            ? Theme.of(context).colorScheme.primary
                            : Theme.of(context).colorScheme.outline,
                      ),
                      const SizedBox(height: 16),
                      Text(
                        _status ?? (_translating ? 'Listening…' : 'Tap to start'),
                        style: Theme.of(context).textTheme.titleMedium,
                        textAlign: TextAlign.center,
                      ),
                    ],
                  ),
                ),
              ),
              FilledButton(
                onPressed: _toggleTranslate,
                child: Text(_translating ? 'Stop' : 'Start translation'),
              ),
              const SizedBox(height: 12),
              OutlinedButton(
                onPressed: () {
                  Navigator.of(context).push(
                    MaterialPageRoute(builder: (_) => const TranslatedCallScreen()),
                  );
                },
                child: const Text('Start translated call'),
              ),
              const SizedBox(height: 8),
              OutlinedButton(
                onPressed: () {
                  Navigator.of(context).push(
                    MaterialPageRoute(builder: (_) => const PhoneCallScreen()),
                  );
                },
                child: const Text('Call with translation'),
              ),
              const SizedBox(height: 16),
              TextButton(
                onPressed: _logout,
                child: const Text('Sign out'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
