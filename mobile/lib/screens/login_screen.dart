import 'dart:io';

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../services/api_client.dart';
import '../services/auth_service.dart';
import '../services/qonversion_service.dart';
import '../services/sso_service.dart';
import 'home_screen.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _emailController = TextEditingController();
  final _passwordController = TextEditingController();
  final _apiKeyController = TextEditingController();
  final _auth = AuthService();
  final _api = ApiClient();
  final _sso = SsoService();
  bool _loading = false;
  String? _error;

  /// Wix page with API key flow — must match the published page URL (see `wix-app/velo-pages/README.md`).
  static const _wixAccountUrl = String.fromEnvironment(
    'WIX_ACCOUNT_URL',
    defaultValue: 'https://www.livetranslate.net/api-key',
  );

  Future<void> _openWixAccount() async {
    final uri = Uri.parse(_wixAccountUrl);
    if (!await launchUrl(uri, mode: LaunchMode.externalApplication)) {
      setState(() {
        _error = 'Could not open website. Please check your connection.';
      });
    }
  }

  Future<void> _loginWithApiKey() async {
    final key = _apiKeyController.text.trim();
    if (key.isEmpty) {
      setState(() {
        _error = 'Please paste your API key from the website account page.';
      });
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final profile = await _api.getMeWithApiKey(key);
      final userId = profile['user_id']?.toString();
      final email = profile['email']?.toString();
      final tier = profile['tier']?.toString() ?? 'free';
      final usage = profile['usage'] as Map<String, dynamic>?;

      await _auth.saveFromAuthResponse({
        'access_token': key,
        'user_id': userId,
        'email': email,
        'tier': tier,
        if (usage != null) 'usage': usage,
      });
      if (QonversionService.isAvailable && userId != null) {
        await QonversionService.identify(userId);
      }
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const HomeScreen()),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString().replaceFirst(RegExp(r'^Exception: '), '');
        _loading = false;
      });
    }
  }

  Future<void> _login() async {
    final email = _emailController.text.trim();
    final password = _passwordController.text;
    if (email.isEmpty || password.isEmpty) {
      setState(() {
        _error = 'Email and password are required';
        _loading = false;
      });
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final body = await _api.login(email, password);
      await _auth.saveFromAuthResponse(body);
      if (QonversionService.isAvailable) {
        final userId = body['user_id'] as String?;
        if (userId != null) await QonversionService.identify(userId);
      }
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const HomeScreen()),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString().replaceFirst(RegExp(r'^Exception: '), '');
        _loading = false;
      });
    }
  }

  Future<void> _signInWithGoogle() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      if (Platform.isWindows || Platform.isMacOS || Platform.isLinux) {
        await _sso.signInWithGoogleDesktop();
      } else {
        await _sso.signInWithGoogle();
      }
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const HomeScreen()),
      );
    } catch (e) {
      if (!mounted) return;
      if (e is SsoException && e.cancelled) {
        setState(() => _loading = false);
        return;
      }
      setState(() {
        _error = e is SsoException ? e.message : e.toString().replaceFirst(RegExp(r'^Exception: '), '');
        _loading = false;
      });
    }
  }

  Future<void> _signInWithApple() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      await _sso.signInWithApple();
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const HomeScreen()),
      );
    } catch (e) {
      if (!mounted) return;
      if (e is SsoException && e.cancelled) {
        setState(() => _loading = false);
        return;
      }
      setState(() {
        _error = e is SsoException ? e.message : e.toString().replaceFirst(RegExp(r'^Exception: '), '');
        _loading = false;
      });
    }
  }

  @override
  void dispose() {
    _emailController.dispose();
    _passwordController.dispose();
    _apiKeyController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final isIOS = Platform.isIOS;
    final isAndroid = Platform.isAndroid;
    final isDesktop =
        Platform.isWindows || Platform.isMacOS || Platform.isLinux;
    final showSsoButtons = isAndroid || isIOS || isDesktop;
    return Scaffold(
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SizedBox(height: 48),
              Text(
                'Live Translate',
                style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                      fontWeight: FontWeight.bold,
                    ),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 8),
              Text(
                'Sign in to use mic translation',
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                      color: Theme.of(context).colorScheme.onSurfaceVariant,
                    ),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 32),
              if (_error != null) ...[
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.errorContainer,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: SelectableText(
                    _error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.onErrorContainer,
                    ),
                  ),
                ),
                const SizedBox(height: 16),
              ],
              if (showSsoButtons) ...[
                // Wix is the primary sign-in path: open website and use API key.
                FilledButton(
                  onPressed: _loading ? null : _openWixAccount,
                  child: const Text('Open API key page (Wix)'),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _apiKeyController,
                  obscureText: true,
                  decoration: const InputDecoration(
                    labelText: 'API key from account page',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                FilledButton(
                  onPressed: _loading ? null : _loginWithApiKey,
                  child: const Text('Use API key'),
                ),
                const SizedBox(height: 24),
                OutlinedButton.icon(
                  onPressed: _loading ? null : _signInWithGoogle,
                  icon: const Icon(Icons.g_mobiledata, size: 24),
                  label: const Text('Continue with Google'),
                  style: OutlinedButton.styleFrom(
                    padding: const EdgeInsets.symmetric(vertical: 12),
                  ),
                ),
                // Apple Sign In: iOS always; Android 13+ (sign_in_with_apple supports it).
                if (isIOS || isAndroid) ...[
                  const SizedBox(height: 12),
                  OutlinedButton.icon(
                    onPressed: _loading ? null : _signInWithApple,
                    icon: const Icon(Icons.apple, size: 24),
                    label: const Text('Continue with Apple'),
                    style: OutlinedButton.styleFrom(
                      padding: const EdgeInsets.symmetric(vertical: 12),
                    ),
                  ),
                ],
                const SizedBox(height: 24),
                Row(
                  children: [
                    const Expanded(child: Divider()),
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      child: Text(
                        'or sign in with email',
                        style: Theme.of(context).textTheme.bodySmall?.copyWith(
                              color: Theme.of(context).colorScheme.onSurfaceVariant,
                            ),
                      ),
                    ),
                    const Expanded(child: Divider()),
                  ],
                ),
                const SizedBox(height: 24),
              ],
              TextField(
                controller: _emailController,
                keyboardType: TextInputType.emailAddress,
                autocorrect: false,
                decoration: const InputDecoration(
                  labelText: 'Email',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: _passwordController,
                obscureText: true,
                decoration: const InputDecoration(
                  labelText: 'Password',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 24),
              FilledButton(
                onPressed: _loading ? null : _login,
                child: _loading
                    ? const SizedBox(
                        height: 20,
                        width: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('Sign in'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
