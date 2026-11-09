import 'dart:async';

import 'package:flutter/material.dart';

import '../../services/api_client.dart';
import 'mic_translate_service.dart';
import 'voice_profiles.dart';

/// Detected speakers → assign TTS voices; default used when unmatched.
class VoiceProfilesPanel extends StatefulWidget {
  const VoiceProfilesPanel({
    super.key,
    required this.manager,
    required this.fallbackVoiceId,
    this.enabled = true,
    this.showHeader = true,
  });

  final VoiceProfileManager manager;
  final String fallbackVoiceId;
  final bool enabled;
  final bool showHeader;

  @override
  State<VoiceProfilesPanel> createState() => _VoiceProfilesPanelState();
}

class _VoiceProfilesPanelState extends State<VoiceProfilesPanel> {
  final _api = ApiClient();
  StreamSubscription<void>? _sub;
  List<_VoiceOption> _voices = const [];
  bool _loadingVoices = true;
  String? _selectedProfileId;

  @override
  void initState() {
    super.initState();
    unawaited(_bootstrap());
    _sub = widget.manager.changes.listen((_) {
      if (!mounted) return;
      setState(() {
        final ids = widget.manager.profiles.map((p) => p.id).toSet();
        if (_selectedProfileId != null &&
            !ids.contains(_selectedProfileId)) {
          _selectedProfileId = null;
        }
      });
    });
  }

  Future<void> _bootstrap() async {
    await widget.manager.init();
    await _loadVoices();
    if (!mounted) return;
    setState(() {});
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }

  Future<void> _loadVoices() async {
    setState(() => _loadingVoices = true);
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
        _loadingVoices = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _loadingVoices = false;
        _voices = [
          _VoiceOption(
            id: widget.fallbackVoiceId.isNotEmpty
                ? widget.fallbackVoiceId
                : MicTranslateService.defaultVoiceId,
            name: 'Default',
            category: 'premade',
          ),
        ];
      });
    }
  }

  Future<void> _renameSelected() async {
    final id = _selectedProfileId;
    if (id == null || !widget.enabled) return;
    final profile = widget.manager.getProfile(id);
    if (profile == null) return;
    final controller = TextEditingController(text: profile.name);
    final name = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Rename profile'),
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
    if (name == null || name.isEmpty) return;
    await widget.manager.renameProfile(id, name);
  }

  Future<void> _deleteSelected() async {
    final id = _selectedProfileId;
    if (id == null || !widget.enabled) return;
    final profile = widget.manager.getProfile(id);
    if (profile == null) return;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete profile?'),
        content: Text('Remove “${profile.name}”? Speaker matching will forget them.'),
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
    if (ok != true) return;
    await widget.manager.deleteProfile(id);
    if (mounted) setState(() => _selectedProfileId = null);
  }

  String _voiceLabel(String? voiceId) {
    if (voiceId == null || voiceId.isEmpty) return 'Default voice';
    for (final v in _voices) {
      if (v.id == voiceId) {
        return v.category == 'premade' ? v.name : '${v.name} (clone)';
      }
    }
    return 'Assigned voice';
  }

  @override
  Widget build(BuildContext context) {
    final profiles = widget.manager.profiles;
    final defaultId = widget.manager.defaultProfileId;
    final activeId = widget.manager.activeProfileId;
    final selected = _selectedProfileId != null &&
            profiles.any((p) => p.id == _selectedProfileId)
        ? _selectedProfileId
        : null;
    final selectedProfile =
        selected == null ? null : widget.manager.getProfile(selected);
    final assignedId = selectedProfile?.assignedVoiceId;
    final assignedInList =
        assignedId == null || _voices.any((v) => v.id == assignedId);
    final defaultInList =
        defaultId != null && profiles.any((p) => p.id == defaultId);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (widget.showHeader) ...[
          Row(
            children: [
              Text(
                'Voice Profiles',
                style: Theme.of(context).textTheme.titleSmall,
              ),
              const Spacer(),
              Text(
                '${profiles.length} profile${profiles.length == 1 ? '' : 's'}',
                style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: Theme.of(context).colorScheme.outline,
                    ),
              ),
            ],
          ),
          const SizedBox(height: 4),
        ],
        Text(
          'Detected speakers get auto-profiles. Assign a TTS voice per speaker; '
          'unmatched speech uses the default profile, then the default voice above.',
          style: Theme.of(context).textTheme.bodySmall?.copyWith(
                color: Theme.of(context).colorScheme.outline,
              ),
        ),
        const SizedBox(height: 8),
        if (profiles.isEmpty)
          Text(
            'Start translating to detect speakers.',
            style: Theme.of(context).textTheme.bodySmall,
          )
        else
          ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 140),
            child: Material(
              color: Theme.of(context).colorScheme.surfaceContainerHighest
                  .withValues(alpha: 0.35),
              borderRadius: BorderRadius.circular(8),
              child: ListView.builder(
                shrinkWrap: true,
                itemCount: profiles.length,
                itemBuilder: (context, i) {
                  final p = profiles[i];
                  final isActive = p.id == activeId;
                  final isSel = p.id == selected;
                  return ListTile(
                    dense: true,
                    selected: isSel,
                    title: Text(p.name),
                    subtitle: Text(_voiceLabel(p.assignedVoiceId)),
                    trailing: isActive
                        ? Text(
                            'Active',
                            style: TextStyle(
                              color: Theme.of(context).colorScheme.primary,
                              fontSize: 12,
                            ),
                          )
                        : null,
                    onTap: widget.enabled
                        ? () => setState(() => _selectedProfileId = p.id)
                        : null,
                  );
                },
              ),
            ),
          ),
        const SizedBox(height: 8),
        DropdownButtonFormField<String?>(
          // ignore: deprecated_member_use
          value: defaultInList ? defaultId : null,
          isExpanded: true,
          decoration: const InputDecoration(
            labelText: 'Default profile (unmatched)',
            border: OutlineInputBorder(),
            contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          ),
          items: [
            const DropdownMenuItem<String?>(
              value: null,
              child: Text('None — use default voice'),
            ),
            for (final p in profiles)
              DropdownMenuItem<String?>(
                value: p.id,
                child: Text(p.name, overflow: TextOverflow.ellipsis),
              ),
          ],
          onChanged: !widget.enabled
              ? null
              : (id) => unawaited(widget.manager.setDefaultProfile(id)),
        ),
        const SizedBox(height: 8),
        DropdownButtonFormField<String?>(
          // ignore: deprecated_member_use
          value: assignedInList ? assignedId : null,
          isExpanded: true,
          decoration: const InputDecoration(
            labelText: 'Voice for selected profile',
            border: OutlineInputBorder(),
            contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          ),
          items: [
            const DropdownMenuItem<String?>(
              value: null,
              child: Text('Use default voice'),
            ),
            for (final v in _voices)
              DropdownMenuItem<String?>(
                value: v.id,
                child: Text(
                  v.category == 'premade' ? v.name : '${v.name} (clone)',
                  overflow: TextOverflow.ellipsis,
                ),
              ),
          ],
          onChanged: !widget.enabled ||
                  selected == null ||
                  _loadingVoices
              ? null
              : (voiceId) => unawaited(
                    widget.manager.setProfileVoice(selected, voiceId),
                  ),
        ),
        const SizedBox(height: 4),
        Wrap(
          spacing: 4,
          children: [
            TextButton.icon(
              onPressed: widget.enabled && selected != null
                  ? _renameSelected
                  : null,
              icon: const Icon(Icons.edit, size: 18),
              label: const Text('Rename'),
            ),
            TextButton.icon(
              onPressed: widget.enabled && selected != null
                  ? _deleteSelected
                  : null,
              icon: const Icon(Icons.delete_outline, size: 18),
              label: const Text('Delete'),
            ),
            TextButton.icon(
              onPressed:
                  widget.enabled && !_loadingVoices ? _loadVoices : null,
              icon: const Icon(Icons.refresh, size: 18),
              label: const Text('Refresh voices'),
            ),
          ],
        ),
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
