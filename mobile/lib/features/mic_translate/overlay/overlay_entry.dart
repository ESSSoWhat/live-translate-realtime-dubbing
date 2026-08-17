import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';

import '../mic_translate_service.dart';
import 'overlay_bridge.dart';

/// Separate Flutter entry point for the Android overlay bubble.
/// Prefer calling [overlayMain] from `main.dart` so the VM entry-point is linked.
void runOverlayTranslateApp() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const OverlayTranslateApp());
}

class OverlayTranslateApp extends StatelessWidget {
  const OverlayTranslateApp({super.key});

  @override
  Widget build(BuildContext context) {
    return const MaterialApp(
      debugShowCheckedModeBanner: false,
      home: OverlayBubble(),
    );
  }
}

/// Bubble pinned to the right edge: captions, volume, voice, mute, stop.
///
/// Drag is intentionally disabled — flutter_overlay_window's native drag
/// listener prevents Flutter from receiving taps.
class OverlayBubble extends StatefulWidget {
  const OverlayBubble({super.key});

  @override
  State<OverlayBubble> createState() => _OverlayBubbleState();
}

class _OverlayBubbleState extends State<OverlayBubble> {
  static const _collapsedSize = 72;
  static const _expandedWidth = 300;
  static const _expandedHeight = 340;

  StreamSubscription<dynamic>? _shareSub;
  bool _bridgeReady = false;
  bool _expanded = false;
  bool _muted = false;
  bool _resizing = false;
  String _status = 'Listening…';
  String _source = '';
  String _translated = '';
  double _fontSize = 14;
  double _opacity = 1;
  double _volume = 1;
  String _voiceId = MicTranslateService.defaultVoiceId;
  List<({String id, String name})> _voices = const [];
  String? _lastUpdateKey;

  @override
  void initState() {
    super.initState();
    OverlayBridge.listenOnOverlay(_onBridgeEvent);
    _shareSub = FlutterOverlayWindow.overlayListener.listen(_onShareEvent);
    OverlayBridge.sendToMain(jsonEncode({'type': 'ready'}));
  }

  @override
  void dispose() {
    _shareSub?.cancel();
    OverlayBridge.disposeOverlay();
    super.dispose();
  }

  void _onBridgeEvent(dynamic event) {
    _bridgeReady = true;
    if (_shareSub != null) {
      unawaited(_shareSub!.cancel());
      _shareSub = null;
    }
    _applyUpdate(event);
  }

  void _onShareEvent(dynamic event) {
    if (_bridgeReady) return;
    _applyUpdate(event);
  }

  void _applyUpdate(dynamic event) {
    Map<String, dynamic>? map;
    if (event is Map) {
      map = Map<String, dynamic>.from(event);
    } else if (event is String) {
      try {
        final decoded = jsonDecode(event);
        if (decoded is Map) {
          map = Map<String, dynamic>.from(decoded);
        }
      } catch (_) {
        return;
      }
    }
    if (map == null || map['type'] != 'update') return;
    if (!mounted) return;

    final status = (map['status'] as String?) ?? _status;
    final source = (map['source'] as String?) ?? _source;
    final translated = (map['translated'] as String?) ?? _translated;
    final muted = (map['muted'] as bool?) ?? _muted;
    var fontSize = _fontSize;
    var opacity = _opacity;
    var volume = _volume;
    var voiceId = _voiceId;
    var voices = _voices;
    final fs = map['fontSize'];
    if (fs is num) fontSize = fs.toDouble();
    final op = map['opacity'];
    if (op is num) opacity = op.toDouble();
    final vol = map['volume'];
    if (vol is num) volume = vol.toDouble().clamp(0.0, 1.0);
    final vid = map['voiceId'];
    if (vid is String && vid.isNotEmpty) voiceId = vid;
    final rawVoices = map['voices'];
    if (rawVoices is List) {
      final parsed = <({String id, String name})>[];
      for (final item in rawVoices) {
        if (item is! Map) continue;
        final id = item['id']?.toString() ?? '';
        if (id.isEmpty) continue;
        final name = item['name']?.toString();
        parsed.add((id: id, name: (name == null || name.isEmpty) ? id : name));
      }
      if (parsed.isNotEmpty) voices = parsed;
    }

    final key =
        '$status|$source|$translated|$muted|$fontSize|$opacity|$volume|$voiceId|${voices.map((v) => v.id).join(',')}';
    if (key == _lastUpdateKey) return;
    _lastUpdateKey = key;

    setState(() {
      _status = status;
      _source = source;
      _translated = translated;
      _muted = muted;
      _fontSize = fontSize;
      _opacity = opacity;
      _volume = volume;
      _voiceId = voiceId;
      _voices = voices;
    });
  }

  void _send(Map<String, dynamic> payload) {
    final encoded = jsonEncode(payload);
    final delivered = OverlayBridge.sendToMain(encoded);
    if (!delivered) {
      unawaited(FlutterOverlayWindow.shareData(encoded));
    }
  }

  Future<void> _toggleExpand() async {
    if (_resizing) return;
    final next = !_expanded;
    setState(() => _expanded = next);
    _resizing = true;
    try {
      await FlutterOverlayWindow.updateFlag(OverlayFlag.focusPointer);
      final w = next ? _expandedWidth : _collapsedSize;
      final h = next ? _expandedHeight : _collapsedSize;
      await FlutterOverlayWindow.resizeOverlay(w, h, false)
          .timeout(const Duration(seconds: 2), onTimeout: () => false);
    } catch (_) {
      // Keep UI state; native resize can fail if overlay was closed.
    } finally {
      if (mounted) _resizing = false;
    }
  }

  void _toggleMute() {
    _send({'type': 'toggleMute'});
  }

  void _setVolume(double value) {
    final v = value.clamp(0.0, 1.0);
    setState(() => _volume = v);
    _send({'type': 'setVolume', 'volume': v});
  }

  void _setVoice(String? id) {
    if (id == null || id.isEmpty) return;
    setState(() => _voiceId = id);
    _send({'type': 'setVoice', 'voiceId': id});
  }

  void _stop() {
    _send({'type': 'stop'});
  }

  String get _selectedVoiceId {
    if (_voices.any((v) => v.id == _voiceId)) return _voiceId;
    if (_voices.isNotEmpty) return _voices.first.id;
    return MicTranslateService.defaultVoiceId;
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    if (!_expanded) {
      return SizedBox(
        width: _collapsedSize.toDouble(),
        height: _collapsedSize.toDouble(),
        child: Material(
          color: scheme.primary,
          shape: const CircleBorder(),
          elevation: 6,
          child: InkWell(
            customBorder: const CircleBorder(),
            onTap: _resizing ? null : _toggleExpand,
            child: Icon(
              _muted ? Icons.mic_off : Icons.translate,
              color: scheme.onPrimary,
              size: 32,
            ),
          ),
        ),
      );
    }

    return Material(
      elevation: 8,
      borderRadius: BorderRadius.circular(16),
      color: scheme.surface,
      child: SizedBox(
        width: _expandedWidth.toDouble(),
        height: _expandedHeight.toDouble(),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 8, 8, 8),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      _status,
                      style: Theme.of(context).textTheme.labelLarge,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  IconButton(
                    visualDensity: VisualDensity.compact,
                    tooltip: 'Collapse',
                    onPressed: _resizing ? null : _toggleExpand,
                    icon: const Icon(Icons.unfold_less),
                  ),
                ],
              ),
              DropdownButtonFormField<String>(
                // ignore: deprecated_member_use
                value: _voices.isEmpty ? null : _selectedVoiceId,
                isExpanded: true,
                decoration: const InputDecoration(
                  isDense: true,
                  labelText: 'Voice',
                  border: OutlineInputBorder(),
                  contentPadding: EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                ),
                items: [
                  for (final v in _voices)
                    DropdownMenuItem<String>(
                      value: v.id,
                      child: Text(v.name, overflow: TextOverflow.ellipsis),
                    ),
                ],
                onChanged: _voices.isEmpty ? null : _setVoice,
              ),
              const SizedBox(height: 4),
              Row(
                children: [
                  Icon(
                    _muted ? Icons.volume_off : Icons.volume_up,
                    size: 20,
                    color: scheme.onSurfaceVariant,
                  ),
                  Expanded(
                    child: Slider(
                      value: _volume,
                      onChanged: _muted
                          ? null
                          : (v) => setState(() => _volume = v.clamp(0.0, 1.0)),
                      onChangeEnd: _muted ? null : _setVolume,
                    ),
                  ),
                  Text(
                    '${(_volume * 100).round()}%',
                    style: Theme.of(context).textTheme.labelSmall,
                  ),
                ],
              ),
              Expanded(
                child: SingleChildScrollView(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      if (_source.isNotEmpty) ...[
                        Text(
                          'Source',
                          style: Theme.of(context).textTheme.labelSmall?.copyWith(
                                color: scheme.onSurfaceVariant,
                              ),
                        ),
                        Text(
                          _source,
                          style: Theme.of(context).textTheme.bodySmall?.copyWith(
                                fontSize: _fontSize,
                                color: scheme.onSurface.withValues(alpha: _opacity),
                              ),
                        ),
                        const SizedBox(height: 8),
                      ],
                      if (_translated.isNotEmpty) ...[
                        Text(
                          'Translation',
                          style: Theme.of(context).textTheme.labelSmall?.copyWith(
                                color: scheme.onSurfaceVariant,
                              ),
                        ),
                        Text(
                          _translated,
                          style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                                fontSize: _fontSize,
                                fontWeight: FontWeight.w600,
                                color: scheme.onSurface.withValues(alpha: _opacity),
                              ),
                        ),
                      ],
                      if (_source.isEmpty && _translated.isEmpty)
                        Text(
                          'Captions appear here while Live Translate runs.',
                          style: Theme.of(context).textTheme.bodySmall?.copyWith(
                                color: scheme.onSurfaceVariant,
                              ),
                        ),
                    ],
                  ),
                ),
              ),
              Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  IconButton(
                    tooltip: _muted ? 'Unmute TTS' : 'Mute TTS',
                    onPressed: _toggleMute,
                    icon: Icon(_muted ? Icons.volume_off : Icons.volume_up),
                  ),
                  FilledButton.tonal(
                    onPressed: _stop,
                    child: const Text('Stop'),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
