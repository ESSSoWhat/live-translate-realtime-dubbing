import 'package:dio/dio.dart';
import 'package:flutter/material.dart';

import '../../services/api_client.dart';
import '../../services/auth_service.dart';
import '../../services/phone_number_hint.dart';

/// Screen for placing a translated phone call via Twilio (PSTN).
/// Your side rings your verified number; you only enter the number to call.
/// On Android the number comes from the phone's SIM (Phone Number Hint) and the
/// SMS code is read with the user's consent; typing is the fallback.
class PhoneCallScreen extends StatefulWidget {
  const PhoneCallScreen({super.key});

  @override
  State<PhoneCallScreen> createState() => _PhoneCallScreenState();
}

class _PhoneCallScreenState extends State<PhoneCallScreen> {
  final _myPhoneController = TextEditingController();
  final _codeController = TextEditingController();
  final _destPhoneController = TextEditingController();
  final _api = ApiClient();
  final _auth = AuthService();
  bool _loading = false;
  bool _checkingPhone = true;
  String? _verifiedPhone;
  String? _codeSentTo;
  String? _status;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadPhone();
  }

  @override
  void dispose() {
    PhoneNumberHint.cancelCodeListener();
    _myPhoneController.dispose();
    _codeController.dispose();
    _destPhoneController.dispose();
    super.dispose();
  }

  /// The API returns `detail` as a string or as `{error, message, ...}`.
  static String _errorText(DioException e) {
    final data = e.response?.data;
    final detail = data is Map ? data['detail'] : null;
    if (detail is String && detail.isNotEmpty) return detail;
    if (detail is Map) {
      final msg = detail['message'];
      if (msg is String && msg.isNotEmpty) return msg;
      if (detail['error'] == 'quota_exceeded') {
        return 'You have used this month\'s allowance.';
      }
    }
    return e.message ?? 'Request failed';
  }

  void _show({String? status, String? error}) {
    if (!mounted) return;
    setState(() {
      _loading = false;
      _status = status;
      _error = error;
    });
  }

  /// Runs [action] with the spinner on; shows API errors in the screen.
  Future<void> _run(Future<void> Function() action) async {
    if (_loading) return;
    if (!await _auth.hasTokens()) {
      _show(error: 'Sign in required');
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
      _status = null;
    });
    try {
      await action();
    } on DioException catch (e) {
      _show(error: _errorText(e));
    } catch (e) {
      _show(error: e.toString());
    }
  }

  Future<void> _loadPhone() async {
    try {
      if (await _auth.hasTokens()) {
        final r = await _api.getVerifiedPhone();
        final phone = r['phone'];
        _verifiedPhone = phone is String && phone.isNotEmpty ? phone : null;
      }
    } catch (_) {
      // Leave unverified; the call endpoint explains what is missing.
    }
    if (mounted) setState(() => _checkingPhone = false);
    if (_verifiedPhone == null) await _useSimNumber();
  }

  /// Offer the SIM's number (Android picker). Picking one sends the code right
  /// away; dismissing it leaves the typed-number form.
  Future<void> _useSimNumber() async {
    if (!PhoneNumberHint.isSupported || _loading || !mounted) return;
    final picked = await PhoneNumberHint.requestPhoneNumber();
    if (picked == null || !mounted) return;
    final e164 = PhoneNumberHint.toE164(picked);
    _myPhoneController.text = e164 ?? picked;
    if (e164 == null) {
      _show(error: 'Check your number is in international format, e.g. +61412345678');
      return;
    }
    await _sendCode();
  }

  Future<void> _sendCode() => _run(() async {
        final phone = _myPhoneController.text.trim();
        if (phone.isEmpty) {
          _show(error: 'Enter your phone number in international format, e.g. +61412345678');
          return;
        }
        final r = await _api.startPhoneVerification(phone);
        final sentTo = r['phone'];
        _codeSentTo = sentTo is String ? sentTo : phone;
        _codeController.clear();
        _show(status: 'We texted a 6-digit code to $_codeSentTo.');
        _autoReadCode();
      });

  /// Android: read the code from the text (after the user taps Allow) and verify.
  Future<void> _autoReadCode() async {
    final code = await PhoneNumberHint.listenForCode();
    if (code == null || !mounted || _codeSentTo == null) return;
    _codeController.text = code;
    await _confirmCode();
  }

  Future<void> _confirmCode() => _run(() async {
        final sentTo = _codeSentTo;
        final code = _codeController.text.trim();
        if (sentTo == null || code.isEmpty) {
          _show(error: 'Enter the code from the text message');
          return;
        }
        final r = await _api.confirmPhoneVerification(sentTo, code);
        final phone = r['phone'];
        _verifiedPhone = phone is String ? phone : sentTo;
        _codeSentTo = null;
        PhoneNumberHint.cancelCodeListener();
        _show(status: 'Your number is verified.');
      });

  Future<void> _removePhone() => _run(() async {
        await _api.removeVerifiedPhone();
        _verifiedPhone = null;
        _show(status: 'Number removed.');
      });

  Future<void> _startCall() => _run(() async {
        final destPhone = _destPhoneController.text.trim();
        if (destPhone.isEmpty) {
          _show(error: 'Enter the number to call');
          return;
        }
        if (mounted) setState(() => _status = 'Starting call…');
        await _api.startTranslatedCall(destPhone: destPhone, targetLang: 'es');
        _show(status: 'Both phones will ring. Answer both for real-time translation.');
      });

  Widget _spinnerOr(String label) => _loading
      ? const SizedBox(
          width: 24,
          height: 24,
          child: CircularProgressIndicator(strokeWidth: 2),
        )
      : Text(label);

  List<Widget> _verifySection(BuildContext context) {
    if (_codeSentTo != null) {
      return [
        Text('Enter the code we texted to $_codeSentTo.', textAlign: TextAlign.center),
        const SizedBox(height: 16),
        TextField(
          controller: _codeController,
          decoration: const InputDecoration(labelText: 'Code'),
          keyboardType: TextInputType.number,
          autofillHints: const [AutofillHints.oneTimeCode],
          maxLength: 6,
        ),
        const SizedBox(height: 8),
        FilledButton(
          onPressed: _loading ? null : _confirmCode,
          child: _spinnerOr('Verify'),
        ),
        TextButton(
          onPressed: _loading
              ? null
              : () {
                  PhoneNumberHint.cancelCodeListener();
                  setState(() => _codeSentTo = null);
                },
          child: const Text('Use a different number'),
        ),
      ];
    }
    return [
      Text(
        'First verify your own phone number. Translated calls always ring this number on your side.',
        style: Theme.of(context).textTheme.bodyMedium,
        textAlign: TextAlign.center,
      ),
      const SizedBox(height: 16),
      TextField(
        controller: _myPhoneController,
        decoration: const InputDecoration(
          labelText: 'Your phone',
          hintText: '+61412345678',
        ),
        keyboardType: TextInputType.phone,
        autofillHints: const [AutofillHints.telephoneNumber],
      ),
      const SizedBox(height: 8),
      FilledButton(
        onPressed: _loading ? null : _sendCode,
        child: _spinnerOr('Text me a code'),
      ),
      if (PhoneNumberHint.isSupported)
        TextButton(
          onPressed: _loading ? null : _useSimNumber,
          child: const Text("Use this phone's number"),
        ),
    ];
  }

  List<Widget> _callSection(BuildContext context) {
    return [
      Text(
        'Your phone: $_verifiedPhone',
        style: Theme.of(context).textTheme.titleMedium,
        textAlign: TextAlign.center,
      ),
      TextButton(
        onPressed: _loading ? null : _removePhone,
        child: const Text('Remove this number'),
      ),
      const SizedBox(height: 8),
      Text(
        'Enter the number to call. Both of you will hear each other translated in real time.',
        style: Theme.of(context).textTheme.bodyMedium,
        textAlign: TextAlign.center,
      ),
      const SizedBox(height: 16),
      TextField(
        controller: _destPhoneController,
        decoration: const InputDecoration(
          labelText: 'Number to call',
          hintText: '+14155550123',
        ),
        keyboardType: TextInputType.phone,
      ),
    ];
  }

  @override
  Widget build(BuildContext context) {
    final verified = _verifiedPhone != null;
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
          child: _checkingPhone
              ? const Center(child: CircularProgressIndicator())
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Expanded(
                      child: ListView(
                        children: [
                          const SizedBox(height: 16),
                          ...(verified ? _callSection(context) : _verifySection(context)),
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
                        ],
                      ),
                    ),
                    if (verified)
                      FilledButton(
                        onPressed: _loading ? null : _startCall,
                        child: _spinnerOr('Call with translation'),
                      ),
                  ],
                ),
        ),
      ),
    );
  }
}
