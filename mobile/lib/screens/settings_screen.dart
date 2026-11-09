import 'package:flutter/material.dart';

import '../services/app_settings.dart';
import '../services/auth_service.dart';
import '../features/translated_call/phone_call_screen.dart';
import '../features/translated_call/translated_call_screen.dart';
import 'login_screen.dart';
import 'manage_plan_screen.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late bool _muteDefault;
  late double _volume;
  late double _fontSize;
  late double _opacity;
  late bool _autoClone;
  late double _autoCloneSeconds;

  @override
  void initState() {
    super.initState();
    _muteDefault = AppSettings.muteTtsDefault;
    _volume = AppSettings.ttsVolume;
    _fontSize = AppSettings.captionFontSize;
    _opacity = AppSettings.captionOpacity;
    _autoClone = AppSettings.autoCloneOnStart;
    _autoCloneSeconds = AppSettings.autoCloneSeconds.toDouble();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        children: [
          const ListTile(
            title: Text('Playback'),
            dense: true,
          ),
          SwitchListTile(
            title: const Text('Mute TTS by default'),
            subtitle: const Text('Captions still update; audio stays off until unmuted'),
            value: _muteDefault,
            onChanged: (v) async {
              setState(() => _muteDefault = v);
              await AppSettings.setMuteTtsDefault(v);
            },
          ),
          ListTile(
            title: const Text('TTS volume'),
            subtitle: Slider(
              value: _volume,
              onChanged: (v) {
                setState(() => _volume = v);
              },
              onChangeEnd: (v) async {
                await AppSettings.setTtsVolume(v);
              },
            ),
          ),
          const Divider(height: 1),
          const ListTile(
            title: Text('Captions'),
            dense: true,
          ),
          ListTile(
            title: Text('Font size (${_fontSize.round()} pt)'),
            subtitle: Slider(
              min: 10,
              max: 28,
              divisions: 18,
              value: _fontSize,
              onChanged: (v) => setState(() => _fontSize = v),
              onChangeEnd: (v) async {
                await AppSettings.setCaptionFontSize(v);
              },
            ),
          ),
          ListTile(
            title: Text('Opacity ${(_opacity * 100).round()}%'),
            subtitle: Slider(
              min: 0.3,
              max: 1.0,
              divisions: 14,
              value: _opacity,
              onChanged: (v) => setState(() => _opacity = v),
              onChangeEnd: (v) async {
                await AppSettings.setCaptionOpacity(v);
              },
            ),
          ),
          const Divider(height: 1),
          const ListTile(
            title: Text('Voice clone'),
            dense: true,
          ),
          SwitchListTile(
            title: const Text('Auto-clone on start'),
            subtitle: const Text(
              'Prompt to record a short sample before each translation session',
            ),
            value: _autoClone,
            onChanged: (v) async {
              setState(() => _autoClone = v);
              await AppSettings.setAutoCloneOnStart(v);
            },
          ),
          ListTile(
            title: Text('Auto-clone length (${_autoCloneSeconds.round()} s)'),
            subtitle: Slider(
              min: 3,
              max: 15,
              divisions: 12,
              value: _autoCloneSeconds,
              onChanged: _autoClone
                  ? (v) => setState(() => _autoCloneSeconds = v)
                  : null,
              onChangeEnd: (v) async {
                await AppSettings.setAutoCloneSeconds(v.round());
              },
            ),
          ),
          const Divider(height: 1),
          ListTile(
            title: const Text('Manage plan'),
            subtitle: const Text('View usage, upgrade or cancel'),
            leading: const Icon(Icons.workspace_premium_outlined),
            trailing: const Icon(Icons.chevron_right),
            onTap: () {
              Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const ManagePlanScreen()),
              );
            },
          ),
          const Divider(height: 1),
          const ListTile(
            title: Text('Experimental'),
            dense: true,
          ),
          ListTile(
            title: const Text('Start translated call'),
            subtitle: const Text('Agora in-app call (beta)'),
            leading: const Icon(Icons.videocam_outlined),
            onTap: () {
              Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const TranslatedCallScreen()),
              );
            },
          ),
          ListTile(
            title: const Text('Call with translation'),
            subtitle: const Text('Twilio phone call (beta)'),
            leading: const Icon(Icons.phone_outlined),
            onTap: () {
              Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const PhoneCallScreen()),
              );
            },
          ),
          const Divider(height: 1),
          const Padding(
            padding: EdgeInsets.all(16),
            child: Text(
              'Modes: Mic Translate (microphone) and Live Translate '
              '(App audio or Screen text OCR + overlay). '
              '“Play as microphone” (VB-Cable) is Windows desktop only. '
              'Some apps block playback capture — see docs/MEDIA_PROJECTION_PHASE2.md.',
              style: TextStyle(fontSize: 13),
            ),
          ),
          const Divider(height: 1),
          ListTile(
            title: const Text('Sign out'),
            leading: const Icon(Icons.logout),
            onTap: () async {
              try {
                await AuthService().clear();
                if (!context.mounted) return;
                Navigator.of(context).pushAndRemoveUntil(
                  MaterialPageRoute(builder: (_) => const LoginScreen()),
                  (_) => false,
                );
              } catch (e) {
                if (!context.mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(content: Text('Sign out failed: $e')),
                );
              }
            },
          ),
        ],
      ),
    );
  }
}
