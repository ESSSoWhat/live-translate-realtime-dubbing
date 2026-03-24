import 'package:dio/dio.dart';
import 'package:flutter/material.dart';

import '../../services/api_client.dart';
import '../../services/auth_service.dart';

/// Screen for placing a translated phone call via Twilio (PSTN).
/// User enters their phone (to receive the call) and destination number.
class PhoneCallScreen extends StatefulWidget {
  const PhoneCallScreen({super.key});

  @override
  State<PhoneCallScreen> createState() => _PhoneCallScreenState();
}

class _PhoneCallScreenState extends State<PhoneCallScreen> {
  final _userPhoneController = TextEditingController();
  final _destPhoneController = TextEditingController();
  final _api = ApiClient();
  final _auth = AuthService();
  bool _loading = false;
  String? _status;
  String? _error;

  @override
  void dispose() {
    _userPhoneController.dispose();
    _destPhoneController.dispose();
    super.dispose();
  }

  Future<void> _startCall() async {
    if (_loading) return;
    if (!await _auth.hasTokens()) {
      setState(() {
        _error = 'Sign in required';
        _status = null;
      });
      return;
    }

    final userPhone = _userPhoneController.text.trim();
    final destPhone = _destPhoneController.text.trim();
    if (userPhone.isEmpty || destPhone.isEmpty) {
      setState(() {
        _error = 'Enter both phone numbers';
        _status = null;
      });
      return;
    }

    setState(() {
      _loading = true;
      _error = null;
      _status = 'Starting call…';
    });

    try {
      await _api.startTranslatedCall(
        userPhone: userPhone,
        destPhone: destPhone,
        targetLang: 'es',
      );
      if (mounted) {
        setState(() {
          _loading = false;
          _status = 'Both phones will ring. Answer both for real-time translation.';
          _error = null;
        });
      }
    } on DioException catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = e.response?.data?['detail'] ?? e.message ?? 'Request failed';
          _status = null;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = e.toString();
          _status = null;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Call with translation'),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => Navigator.of(context).pop(),
        ),
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SizedBox(height: 16),
              Text(
                'Enter your phone number (to receive the call) and the number to call. '
                'Both parties will hear each other translated in real time.',
                style: Theme.of(context).textTheme.bodyMedium,
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 24),
              TextField(
                controller: _userPhoneController,
                decoration: const InputDecoration(
                  labelText: 'Your phone',
                  hintText: '+1234567890',
                ),
                keyboardType: TextInputType.phone,
              ),
              const SizedBox(height: 16),
              TextField(
                controller: _destPhoneController,
                decoration: const InputDecoration(
                  labelText: 'Number to call',
                  hintText: '+0987654321',
                ),
                keyboardType: TextInputType.phone,
              ),
              if (_error != null) ...[
                const SizedBox(height: 16),
                Text(
                  _error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                  textAlign: TextAlign.center,
                ),
              ],
              if (_status != null) ...[
                const SizedBox(height: 16),
                Text(
                  _status!,
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                        color: Colors.green,
                      ),
                  textAlign: TextAlign.center,
                ),
              ],
              const Spacer(),
              FilledButton(
                onPressed: _loading ? null : _startCall,
                child: _loading
                    ? const SizedBox(
                        width: 24,
                        height: 24,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('Call with translation'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
