import 'dart:async';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

import '../../services/api_client.dart';
import 'mic_translate_service.dart';

/// Compact voice picker + clone / import / rename / delete for Home.
class VoicePickerBar extends StatefulWidget {
  const VoicePickerBar({
    super.key,
    required this.selectedVoiceId,
    required this.onVoiceSelected,
    this.enabled = true,
  });

  final String selectedVoiceId;
  final ValueChanged<String> onVoiceSelected;
  final bool enabled;

  @override
  State<VoicePickerBar> createState() => _VoicePickerBarState();
}

class _VoicePickerBarState extends State<VoicePickerBar> {
  final _api = ApiClient();
  List<_VoiceOption> _voices = const [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadVoices();
  }

  bool get _selectedIsClone {
    for (final v in _voices) {
      if (v.id == widget.selectedVoiceId) {
        return v.category != 'premade';
      }
    }
    return false;
  }

  Future<void> _loadVoices({String? preferVoiceId}) async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final raw = await _api.getVoices();
      final options = raw
          .map(
            (m) => _VoiceOption(
              id: (m['voice_id'] as String?) ?? '',
              name: (m['name'] as String?) ?? 'Voice',
              category: (m['category'] as String?) ?? 'premade',
            ),
          )
          .where((v) => v.id.isNotEmpty)
          .toList();
      options.sort((a, b) {
        final ac = a.category == 'cloned' || a.category == 'generated' ? 0 : 1;
        final bc = b.category == 'cloned' || b.category == 'generated' ? 0 : 1;
        if (ac != bc) return ac.compareTo(bc);
        return a.name.toLowerCase().compareTo(b.name.toLowerCase());
      });
      if (!mounted) return;
      setState(() {
        _voices = options;
        _loading = false;
      });
      final prefer = preferVoiceId ?? widget.selectedVoiceId;
      if (prefer.isNotEmpty && options.any((v) => v.id == prefer)) {
        widget.onVoiceSelected(prefer);
      } else if (options.isNotEmpty &&
          !options.any((v) => v.id == widget.selectedVoiceId)) {
        widget.onVoiceSelected(options.first.id);
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = 'Could not load voices';
        _voices = [
          const _VoiceOption(
            id: MicTranslateService.defaultVoiceId,
            name: 'Default (Rachel)',
            category: 'premade',
          ),
        ];
      });
    }
  }

  Future<void> _openCloneDialog() async {
    if (!widget.enabled) return;
    final result = await showDialog<_CloneResult>(
      context: context,
      barrierDismissible: false,
      builder: (_) => const _CloneVoiceDialog(),
    );
    if (result == null || !mounted) return;
    await _loadVoices(preferVoiceId: result.voiceId);
    widget.onVoiceSelected(result.voiceId);
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('Voice cloned: ${result.name}')),
    );
  }

  Future<void> _importSample() async {
    if (!widget.enabled) return;
    try {
      final picked = await FilePicker.platform.pickFiles(
        type: FileType.custom,
        allowedExtensions: const ['wav', 'mp3', 'm4a', 'ogg', 'flac'],
        withData: true,
      );
      if (picked == null || picked.files.isEmpty) return;
      final file = picked.files.first;
      final bytes = file.bytes;
      if (bytes == null || bytes.isEmpty) {
        throw Exception('Could not read selected file');
      }
      final nameController = TextEditingController(
        text: (file.name.split('.').first).trim().isEmpty
            ? 'Imported voice'
            : file.name.split('.').first,
      );
      if (!mounted) return;
      final name = await showDialog<String>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('Import voice sample'),
          content: TextField(
            controller: nameController,
            decoration: const InputDecoration(
              labelText: 'Voice name',
              border: OutlineInputBorder(),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(ctx, nameController.text.trim()),
              child: const Text('Clone'),
            ),
          ],
        ),
      );
      nameController.dispose();
      if (name == null || name.isEmpty || !mounted) return;

      showDialog<void>(
        context: context,
        barrierDismissible: false,
        builder: (_) => const Center(child: CircularProgressIndicator()),
      );
      final body = await _api.cloneVoice(
        audioBytes: bytes,
        name: name,
        description: 'Imported from Live Translate mobile',
        filename: file.name.isEmpty ? 'audio.wav' : file.name,
      );
      if (mounted) Navigator.of(context).pop();
      final voiceId = body['voice_id'] as String?;
      if (voiceId == null || voiceId.isEmpty) {
        throw Exception('Clone succeeded but no voice_id returned');
      }
      await _loadVoices(preferVoiceId: voiceId);
      widget.onVoiceSelected(voiceId);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Voice imported: ${body['name'] ?? name}')),
      );
    } catch (e) {
      if (mounted && Navigator.of(context).canPop()) {
        Navigator.of(context).pop();
      }
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(_err(e))),
      );
    }
  }

  Future<void> _renameSelected() async {
    if (!widget.enabled || !_selectedIsClone) return;
    final current = _voices.firstWhere((v) => v.id == widget.selectedVoiceId);
    final controller = TextEditingController(text: current.name);
    final name = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Rename voice'),
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(
            labelText: 'Name',
            border: OutlineInputBorder(),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim()),
            child: const Text('Save'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (name == null || name.isEmpty || !mounted) return;
    try {
      await _api.renameVoice(voiceId: current.id, name: name);
      await _loadVoices(preferVoiceId: current.id);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Renamed to $name')),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(_err(e))),
      );
    }
  }

  Future<void> _deleteSelected() async {
    if (!widget.enabled || !_selectedIsClone) return;
    final current = _voices.firstWhere((v) => v.id == widget.selectedVoiceId);
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete voice?'),
        content: Text('Delete cloned voice “${current.name}”? This cannot be undone.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      await _api.deleteVoice(current.id);
      await _loadVoices(preferVoiceId: MicTranslateService.defaultVoiceId);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Voice deleted')),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(_err(e))),
      );
    }
  }

  String _err(Object e) {
    if (e is DioException) {
      final detail = e.response?.data;
      if (detail is Map && detail['detail'] != null) {
        return detail['detail'].toString();
      }
      return e.message ?? e.toString();
    }
    return e.toString().replaceFirst(RegExp(r'^Exception: '), '');
  }

  @override
  Widget build(BuildContext context) {
    final selected = _voices.any((v) => v.id == widget.selectedVoiceId)
        ? widget.selectedVoiceId
        : (_voices.isNotEmpty
            ? _voices.first.id
            : MicTranslateService.defaultVoiceId);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Expanded(
              child: DropdownButtonFormField<String>(
                // ignore: deprecated_member_use
                value: _voices.isEmpty ? null : selected,
                isExpanded: true,
                decoration: const InputDecoration(
                  labelText: 'Voice',
                  border: OutlineInputBorder(),
                  contentPadding:
                      EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                ),
                items: [
                  for (final v in _voices)
                    DropdownMenuItem(
                      value: v.id,
                      child: Text(
                        v.category == 'premade' ? v.name : '${v.name} (clone)',
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                ],
                onChanged: !widget.enabled || _loading || _voices.isEmpty
                    ? null
                    : (id) {
                        if (id != null) widget.onVoiceSelected(id);
                      },
              ),
            ),
            const SizedBox(width: 4),
            IconButton(
              tooltip: 'Refresh',
              onPressed: widget.enabled && !_loading ? () => _loadVoices() : null,
              icon: _loading
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.refresh),
            ),
          ],
        ),
        const SizedBox(height: 4),
        Wrap(
          spacing: 4,
          children: [
            TextButton.icon(
              onPressed: widget.enabled && !_loading ? _openCloneDialog : null,
              icon: const Icon(Icons.mic, size: 18),
              label: const Text('Clone'),
            ),
            TextButton.icon(
              onPressed: widget.enabled && !_loading ? _importSample : null,
              icon: const Icon(Icons.upload_file, size: 18),
              label: const Text('Import'),
            ),
            TextButton.icon(
              onPressed: widget.enabled && !_loading && _selectedIsClone
                  ? _renameSelected
                  : null,
              icon: const Icon(Icons.edit, size: 18),
              label: const Text('Rename'),
            ),
            TextButton.icon(
              onPressed: widget.enabled && !_loading && _selectedIsClone
                  ? _deleteSelected
                  : null,
              icon: const Icon(Icons.delete_outline, size: 18),
              label: const Text('Delete'),
            ),
          ],
        ),
        if (_error != null) ...[
          Text(
            _error!,
            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                  color: Theme.of(context).colorScheme.error,
                ),
          ),
        ],
      ],
    );
  }
}

class _VoiceOption {
  const _VoiceOption({
    required this.id,
    required this.name,
    required this.category,
  });

  final String id;
  final String name;
  final String category;
}

class _CloneResult {
  const _CloneResult({required this.voiceId, required this.name});
  final String voiceId;
  final String name;
}

class _CloneVoiceDialog extends StatefulWidget {
  const _CloneVoiceDialog({this.sampleSeconds = 8});

  final int sampleSeconds;

  @override
  State<_CloneVoiceDialog> createState() => _CloneVoiceDialogState();
}

class _CloneVoiceDialogState extends State<_CloneVoiceDialog> {
  final _api = ApiClient();
  final _nameController = TextEditingController(text: 'My voice');
  final _record = AudioRecorder();
  bool _recording = false;
  bool _uploading = false;
  int _secondsLeft = 8;
  String? _error;
  Timer? _tick;

  int get _sampleSeconds => widget.sampleSeconds;

  @override
  void initState() {
    super.initState();
    _secondsLeft = _sampleSeconds;
  }

  @override
  void dispose() {
    _tick?.cancel();
    _nameController.dispose();
    _record.dispose();
    super.dispose();
  }

  Future<void> _recordAndClone() async {
    if (_recording || _uploading) return;
    setState(() {
      _error = null;
      _recording = true;
      _secondsLeft = _sampleSeconds;
    });

    String? path;
    try {
      if (!await _record.hasPermission()) {
        throw Exception('Microphone permission denied');
      }
      final dir = await getTemporaryDirectory();
      path =
          '${dir.path}/clone_sample_${DateTime.now().millisecondsSinceEpoch}.wav';
      await _record.start(
        const RecordConfig(
          encoder: AudioEncoder.wav,
          sampleRate: 16000,
          numChannels: 1,
          audioInterruption: AudioInterruptionMode.none,
          echoCancel: true,
          noiseSuppress: true,
          androidConfig: AndroidRecordConfig(
            manageBluetooth: false,
            audioSource: AndroidAudioSource.voiceRecognition,
          ),
        ),
        path: path,
      );

      _tick = Timer.periodic(const Duration(seconds: 1), (t) {
        if (!mounted) {
          t.cancel();
          return;
        }
        setState(
          () => _secondsLeft = (_sampleSeconds - t.tick).clamp(0, _sampleSeconds),
        );
        if (t.tick >= _sampleSeconds) t.cancel();
      });

      await Future<void>.delayed(Duration(seconds: _sampleSeconds));
      await _record.stop();
      _tick?.cancel();

      if (!mounted) return;
      setState(() {
        _recording = false;
        _uploading = true;
        _secondsLeft = 0;
      });

      final bytes = await File(path).readAsBytes();
      if (bytes.isEmpty) {
        throw Exception('Recording was empty — try again closer to the mic');
      }

      final body = await _api.cloneVoice(
        audioBytes: bytes,
        name: _nameController.text.trim().isEmpty
            ? 'My voice'
            : _nameController.text.trim(),
        description: 'Cloned from Live Translate mobile',
      );
      final voiceId = body['voice_id'] as String?;
      final name = (body['name'] as String?) ?? _nameController.text.trim();
      if (voiceId == null || voiceId.isEmpty) {
        throw Exception('Clone succeeded but no voice_id returned');
      }
      if (!mounted) return;
      Navigator.of(context).pop(_CloneResult(voiceId: voiceId, name: name));
    } catch (e) {
      if (!mounted) return;
      String message;
      if (e is DioException) {
        final detail = e.response?.data;
        if (detail is Map && detail['detail'] != null) {
          message = detail['detail'].toString();
        } else {
          message = e.message ?? e.toString();
        }
      } else {
        message = e.toString().replaceFirst(RegExp(r'^Exception: '), '');
      }
      setState(() {
        _recording = false;
        _uploading = false;
        _error = message;
        _secondsLeft = _sampleSeconds;
      });
    } finally {
      _tick?.cancel();
      if (path != null) {
        try {
          await File(path).delete();
        } catch (_) {}
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final busy = _recording || _uploading;
    return AlertDialog(
      title: const Text('Clone voice'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            'Speak naturally for $_sampleSeconds seconds. '
            'This sample is sent to create a cloned TTS voice.',
            style: Theme.of(context).textTheme.bodyMedium,
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _nameController,
            enabled: !busy,
            decoration: const InputDecoration(
              labelText: 'Voice name',
              border: OutlineInputBorder(),
            ),
          ),
          if (_recording) ...[
            const SizedBox(height: 16),
            Text(
              'Recording… $_secondsLeft s',
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            LinearProgressIndicator(
              value: (_sampleSeconds - _secondsLeft) / _sampleSeconds,
            ),
          ],
          if (_uploading) ...[
            const SizedBox(height: 16),
            const Center(child: CircularProgressIndicator()),
            const SizedBox(height: 8),
            const Text('Creating voice…', textAlign: TextAlign.center),
          ],
          if (_error != null) ...[
            const SizedBox(height: 12),
            Text(
              _error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ],
        ],
      ),
      actions: [
        TextButton(
          onPressed: busy ? null : () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: busy ? null : _recordAndClone,
          child: Text(_recording ? 'Recording…' : 'Record & clone'),
        ),
      ],
    );
  }
}

/// Shows the clone dialog and returns the new voice id, or null.
Future<String?> showCloneVoiceDialog(
  BuildContext context, {
  int sampleSeconds = 8,
}) async {
  final result = await showDialog<_CloneResult>(
    context: context,
    barrierDismissible: false,
    builder: (_) => _CloneVoiceDialog(sampleSeconds: sampleSeconds),
  );
  return result?.voiceId;
}
