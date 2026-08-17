import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';

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

/// Bubble pinned to the right edge: status, live captions, mute TTS, stop.
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
  static const _expandedWidth = 280;
  static const _expandedHeight = 220;

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
    final fs = map['fontSize'];
    if (fs is num) fontSize = fs.toDouble();
    final op = map['opacity'];
    if (op is num) opacity = op.toDouble();

    final key = '$status|$source|$translated|$muted|$fontSize|$opacity';
    if (key == _lastUpdateKey) return;
    _lastUpdateKey = key;

    setState(() {
      _status = status;
      _source = source;
      _translated = translated;
      _muted = muted;
      _fontSize = fontSize;
      _opacity = opacity;
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
      // Third arg enableDrag=false — required for taps to keep working.
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

  void _stop() {
    _send({'type': 'stop'});
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
                    onPressed: _resizing ? null : _toggleExpand,
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
