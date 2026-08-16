import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';

import '../config/languages.dart';
import '../features/mic_translate/mic_translate_service.dart';
import '../features/mic_translate/overlay/overlay_translate_controller.dart';
import '../features/mic_translate/voice_picker_bar.dart';
import '../services/api_client.dart';
import '../services/app_settings.dart';
import '../services/auth_service.dart';
import '../services/qonversion_service.dart';
import '../widgets/usage_card.dart';
import 'login_screen.dart';
import 'paywall_screen.dart';
import 'settings_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> with WidgetsBindingObserver {
  final _auth = AuthService();
  final _api = ApiClient();
  late final MicTranslateService _translateService;
  late final OverlayTranslateController _overlayController;
  bool _translating = false;
  bool _muted = false;
  String? _status;
  String _sourceLanguage = 'auto';
  String _targetLanguage = 'es';
  String _voiceId = MicTranslateService.defaultVoiceId;
  String _sourceCaption = '';
  String _translatedCaption = '';
  final _sourceScroll = ScrollController();
  final _translatedScroll = ScrollController();
  StreamSubscription<String>? _statusSub;
  StreamSubscription<String>? _sourceSub;
  StreamSubscription<String>? _translatedSub;
  StreamSubscription<void>? _paywallSub;
  StreamSubscription<bool>? _activeSub;
  bool _isToggling = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _sourceLanguage = AppSettings.sourceLanguage;
    _targetLanguage = AppSettings.targetLanguage;
    if (!kSourceLanguages.any((l) => l.code == _sourceLanguage)) {
      _sourceLanguage = 'auto';
    }
    if (!kSupportedLanguages.any((l) => l.code == _targetLanguage)) {
      _targetLanguage = 'es';
    }
    _voiceId = AppSettings.voiceId;
    _muted = AppSettings.muteTtsDefault;
    _translateService = MicTranslateService(
      sourceLanguage: _sourceLanguage,
      targetLanguage: _targetLanguage,
      voiceId: _voiceId,
    );
    _translateService.muted = _muted;
    _translateService.volume = AppSettings.ttsVolume;
    _overlayController = OverlayTranslateController(service: _translateService);
    _statusSub = _translateService.statusStream.listen((s) {
      if (mounted) setState(() => _status = s);
    });
    _sourceSub = _translateService.sourceTextStream.listen((t) {
      if (!mounted) return;
      setState(() => _sourceCaption = t);
      _scrollToEnd(_sourceScroll);
    });
    _translatedSub = _translateService.translatedTextStream.listen((t) {
      if (!mounted) return;
      setState(() => _translatedCaption = t);
      _scrollToEnd(_translatedScroll);
    });
    _paywallSub = _translateService.paywallRequiredStream.listen((_) {
      if (mounted) _showPaywall();
    });
    _activeSub = _overlayController.activeStream.listen((active) {
      if (!mounted) return;
      setState(() {
        _translating = active;
        if (!active) _status = null;
      });
    });
  }

  void _scrollToEnd(ScrollController c) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!c.hasClients) return;
      c.animateTo(
        c.position.maxScrollExtent,
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
      );
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _statusSub?.cancel();
    _sourceSub?.cancel();
    _translatedSub?.cancel();
    _paywallSub?.cancel();
    _activeSub?.cancel();
    _sourceScroll.dispose();
    _translatedScroll.dispose();
    unawaited(_overlayController.stop());
    _overlayController.dispose();
    _translateService.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && _translating) {
      unawaited(_overlayController.retryOverlayIfNeeded());
    }
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

  void _setSourceLanguage(String? code) {
    if (code == null) return;
    setState(() => _sourceLanguage = code);
    _translateService.sourceLanguage = code;
    unawaited(AppSettings.setSourceLanguage(code));
  }

  void _setTargetLanguage(String? code) {
    if (code == null) return;
    setState(() => _targetLanguage = code);
    _translateService.targetLanguage = code;
    unawaited(AppSettings.setTargetLanguage(code));
  }

  void _setVoiceId(String id) {
    setState(() => _voiceId = id);
    _translateService.voiceId = id;
    unawaited(AppSettings.setVoiceId(id));
  }

  void _toggleMute() {
    final next = !_muted;
    setState(() => _muted = next);
    _translateService.muted = next;
  }

  void _clearCaptions() {
    setState(() {
      _sourceCaption = '';
      _translatedCaption = '';
    });
  }

  void _swapLanguages() {
    if (_sourceLanguage == 'auto') return;
    final nextSource = _targetLanguage;
    final nextTarget = _sourceLanguage;
    setState(() {
      _sourceLanguage = nextSource;
      _targetLanguage = nextTarget;
    });
    _translateService.sourceLanguage = nextSource;
    _translateService.targetLanguage = nextTarget;
    unawaited(AppSettings.setSourceLanguage(nextSource));
    unawaited(AppSettings.setTargetLanguage(nextTarget));
  }

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

  Future<void> _maybeAutoClone() async {
    if (!AppSettings.autoCloneOnStart) return;
    if (!mounted) return;
    setState(() => _status = 'Auto-cloning voice…');
    final voiceId = await showCloneVoiceDialog(
      context,
      sampleSeconds: AppSettings.autoCloneSeconds,
    );
    if (voiceId != null) {
      _setVoiceId(voiceId);
    }
  }

  Future<void> _toggleTranslate() async {
    if (_isToggling) return;
    _isToggling = true;
    try {
      if (_translating) {
        await _overlayController.stop();
        if (mounted) {
          setState(() {
            _translating = false;
            _status = null;
          });
        }
        return;
      }
      // Refresh settings that may have changed on Settings screen.
      _translateService.volume = AppSettings.ttsVolume;
      _translateService.muted = _muted;

      if (mounted) setState(() => _status = 'Checking access…');
      if (!await _hasPremiumAccess()) {
        if (!mounted) return;
        setState(() => _status = null);
        _showPaywall();
        return;
      }
      await _maybeAutoClone();
      if (!mounted) return;
      if (mounted) setState(() => _status = 'Starting…');
      final result = await _overlayController.start();
      if (!mounted) return;
      setState(() {
        _translating = result.started;
        if (!result.started) {
          _status = 'Could not start — check microphone permission';
        }
      });
      if (result.started && !result.overlayShown && Platform.isAndroid) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
              'Translation started. Enable “Display over other apps” for Live Translate '
              'to show the caption bubble over other apps.',
            ),
            duration: Duration(seconds: 5),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _translating = false;
          _status = null;
        });
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Could not start translation: $e')),
        );
      }
    } finally {
      _isToggling = false;
    }
  }

  Future<void> _logout() async {
    await _overlayController.stop();
    await _auth.clear();
    if (!mounted) return;
    Navigator.of(context).pushAndRemoveUntil(
      MaterialPageRoute(builder: (_) => const LoginScreen()),
      (_) => false,
    );
  }

  Widget _languageDropdown({
    required String label,
    required String value,
    required List<AppLanguage> languages,
    required ValueChanged<String?> onChanged,
  }) {
    return Expanded(
      child: DropdownButtonFormField<String>(
        // Controlled selection — `value` keeps swap/settings updates stable.
        // ignore: deprecated_member_use
        value: value,
        isExpanded: true,
        decoration: InputDecoration(
          labelText: label,
          border: const OutlineInputBorder(),
          contentPadding:
              const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        ),
        items: [
          for (final lang in languages)
            DropdownMenuItem(value: lang.code, child: Text(lang.name)),
        ],
        onChanged: onChanged,
      ),
    );
  }

  Widget _captionPane({
    required String title,
    required String text,
    required ScrollController controller,
  }) {
    final fontSize = AppSettings.captionFontSize;
    final opacity = AppSettings.captionOpacity;
    return Expanded(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(title, style: Theme.of(context).textTheme.labelLarge),
          const SizedBox(height: 4),
          Expanded(
            child: DecoratedBox(
              decoration: BoxDecoration(
                border: Border.all(
                  color: Theme.of(context).colorScheme.outlineVariant,
                ),
                borderRadius: BorderRadius.circular(8),
              ),
              child: SingleChildScrollView(
                controller: controller,
                padding: const EdgeInsets.all(10),
                child: Text(
                  text.isEmpty ? '—' : text,
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                        fontSize: fontSize,
                        color: Theme.of(context)
                            .colorScheme
                            .onSurface
                            .withValues(alpha: opacity),
                      ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Live Translate'),
        actions: [
          IconButton(
            tooltip: _muted ? 'Unmute TTS' : 'Mute TTS',
            icon: Icon(_muted ? Icons.volume_off : Icons.volume_up),
            onPressed: _toggleMute,
          ),
          IconButton(
            icon: const Icon(Icons.settings),
            onPressed: () async {
              await Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const SettingsScreen()),
              );
              if (!mounted) return;
              setState(() {});
              _translateService.volume = AppSettings.ttsVolume;
            },
          ),
        ],
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const UsageCard(),
              const SizedBox(height: 12),
              Row(
                children: [
                  _languageDropdown(
                    label: 'From',
                    value: _sourceLanguage,
                    languages: kSourceLanguages,
                    onChanged: _setSourceLanguage,
                  ),
                  IconButton(
                    tooltip: _sourceLanguage == 'auto'
                        ? 'Pick a source language to swap'
                        : 'Swap languages',
                    onPressed: _sourceLanguage == 'auto' ? null : _swapLanguages,
                    icon: const Icon(Icons.swap_horiz),
                  ),
                  _languageDropdown(
                    label: 'To',
                    value: _targetLanguage,
                    languages: kSupportedLanguages,
                    onChanged: _setTargetLanguage,
                  ),
                ],
              ),
              const SizedBox(height: 8),
              VoicePickerBar(
                selectedVoiceId: _voiceId,
                onVoiceSelected: _setVoiceId,
                enabled: !_translating,
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  Icon(
                    _translating ? Icons.mic : Icons.mic_none,
                    color: _translating
                        ? Theme.of(context).colorScheme.primary
                        : Theme.of(context).colorScheme.outline,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      _status ??
                          (_translating ? 'Listening…' : 'Tap Start to translate'),
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
                  ),
                  TextButton(
                    onPressed: _clearCaptions,
                    child: const Text('Clear'),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Expanded(
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _captionPane(
                      title: 'Source',
                      text: _sourceCaption,
                      controller: _sourceScroll,
                    ),
                    const SizedBox(width: 8),
                    _captionPane(
                      title: 'Translation',
                      text: _translatedCaption,
                      controller: _translatedScroll,
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 12),
              FilledButton(
                onPressed: _toggleTranslate,
                child: Text(_translating ? 'Stop' : 'Start translation'),
              ),
              const SizedBox(height: 8),
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
