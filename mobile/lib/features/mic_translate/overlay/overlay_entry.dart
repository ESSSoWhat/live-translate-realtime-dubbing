import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';

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

/// Draggable bubble: status, live captions, mute TTS, stop.
class OverlayBubble extends StatefulWidget {
  const OverlayBubble({super.key});

  @override
  State<OverlayBubble> createState() => _OverlayBubbleState();
}

class _OverlayBubbleState extends State<OverlayBubble> {
  static const _collapsedSize = 72;
  static const _expandedWidth = 280;
  static const _expandedHeight = 220;

  StreamSubscription<dynamic>? _sub;
  bool _expanded = false;
  bool _muted = false;
  String _status = 'Listening…';
  String _source = '';
  String _translated = '';

  @override
  void initState() {
    super.initState();
    _sub = FlutterOverlayWindow.overlayListener.listen(_onEvent);
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }

  void _onEvent(dynamic event) {
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
    setState(() {
      _status = (map!['status'] as String?) ?? _status;
      _source = (map['source'] as String?) ?? _source;
      _translated = (map['translated'] as String?) ?? _translated;
      _muted = (map['muted'] as bool?) ?? _muted;
    });
  }

  Future<void> _send(Map<String, dynamic> payload) async {
    await FlutterOverlayWindow.shareData(jsonEncode(payload));
  }

  Future<void> _toggleExpand() async {
    final next = !_expanded;
    setState(() => _expanded = next);
    if (next) {
      await FlutterOverlayWindow.resizeOverlay(
        _expandedWidth,
        _expandedHeight,
        true,
      );
    } else {
      await FlutterOverlayWindow.resizeOverlay(
        _collapsedSize,
        _collapsedSize,
        true,
      );
    }
  }

  Future<void> _toggleMute() async {
    await _send({'type': 'toggleMute'});
  }

  Future<void> _stop() async {
    await _send({'type': 'stop'});
  }

  @override
  Widget build(BuildContext context) {
    if (!_expanded) {
      return Material(
        color: Colors.transparent,
        child: GestureDetector(
          onTap: _toggleExpand,
          child: Container(
            width: _collapsedSize.toDouble(),
            height: _collapsedSize.toDouble(),
            decoration: BoxDecoration(
              color: Theme.of(context).colorScheme.primary,
              shape: BoxShape.circle,
              boxShadow: const [
                BoxShadow(
                  color: Color(0x40000000),
                  blurRadius: 8,
                  offset: Offset(0, 2),
                ),
              ],
            ),
            child: Icon(
              _muted ? Icons.mic_off : Icons.translate,
              color: Theme.of(context).colorScheme.onPrimary,
              size: 32,
            ),
          ),
        ),
      );
    }

    final scheme = Theme.of(context).colorScheme;
    return Material(
      elevation: 8,
      borderRadius: BorderRadius.circular(16),
      color: scheme.surface,
      child: Padding(
        padding: const EdgeInsets.all(12),
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
                  onPressed: _toggleExpand,
                  icon: const Icon(Icons.unfold_less),
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
                      Text(_source, style: Theme.of(context).textTheme.bodySmall),
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
                              fontWeight: FontWeight.w600,
                            ),
                      ),
                    ],
                    if (_source.isEmpty && _translated.isEmpty)
                      Text(
                        'Speak near the mic — captions appear here.',
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
    );
  }
}
