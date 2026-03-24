import 'package:flutter/material.dart';

import 'translated_call_service.dart';

/// Screen for starting a translated in-app call (Agora with TTS as custom mic).
class TranslatedCallScreen extends StatefulWidget {
  const TranslatedCallScreen({super.key});

  @override
  State<TranslatedCallScreen> createState() => _TranslatedCallScreenState();
}

class _TranslatedCallScreenState extends State<TranslatedCallScreen> {
  final _channelController = TextEditingController(text: 'translated_call_1');
  final _service = TranslatedCallService(targetLanguage: 'es');
  String? _status;

  @override
  void initState() {
    super.initState();
    _service.statusStream.listen((s) {
      if (mounted) setState(() => _status = s);
    });
  }

  @override
  void dispose() {
    _channelController.dispose();
    _service.dispose();
    super.dispose();
  }

  Future<void> _toggleCall() async {
    if (_service.inCall) {
      await _service.stop();
      if (mounted) setState(() {});
      return;
    }
    final channelId = _channelController.text.trim();
    if (channelId.isEmpty) {
      setState(() => _status = 'Enter channel ID');
      return;
    }
    await _service.startTranslatedCall(channelId);
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Translated call'),
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
              const SizedBox(height: 24),
              Text(
                'Start a call where others hear you in the target language. '
                'Join from another device with the same channel ID.',
                style: Theme.of(context).textTheme.bodyMedium,
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 24),
              TextField(
                controller: _channelController,
                decoration: const InputDecoration(
                  labelText: 'Channel ID',
                  hintText: 'e.g. translated_call_1',
                ),
              ),
              const SizedBox(height: 24),
              Expanded(
                child: Center(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(
                        _service.inCall ? Icons.call : Icons.call_end,
                        size: 64,
                        color: _service.inCall
                            ? Colors.green
                            : Theme.of(context).colorScheme.outline,
                      ),
                      const SizedBox(height: 16),
                      Text(
                        _status ?? (_service.inCall ? 'In call' : 'Ready'),
                        style: Theme.of(context).textTheme.titleMedium,
                        textAlign: TextAlign.center,
                      ),
                    ],
                  ),
                ),
              ),
              FilledButton(
                onPressed: _toggleCall,
                style: FilledButton.styleFrom(
                  backgroundColor: _service.inCall ? Colors.red : null,
                ),
                child: Text(_service.inCall ? 'End call' : 'Start translated call'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
