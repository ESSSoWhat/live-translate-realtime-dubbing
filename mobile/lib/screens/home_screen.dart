import 'dart:async';

import 'package:flutter/material.dart';

import '../services/auth_service.dart';
import 'login_screen.dart';
import 'paywall_screen.dart';
import 'settings_screen.dart';
import '../features/mic_translate/mic_translate_service.dart';
import '../services/qonversion_service.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final _auth = AuthService();
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
      if (QonversionService.isAvailable &&
          !await QonversionService.checkEntitlements()) {
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
