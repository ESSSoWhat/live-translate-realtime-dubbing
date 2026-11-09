import 'dart:isolate';
import 'dart:ui';

/// Reliable main-app ↔ overlay-window messaging via [IsolateNameServer].
/// Prefer this over [FlutterOverlayWindow.shareData], which is flaky across engines.
class OverlayBridge {
  OverlayBridge._();

  static const mainPortName = 'lt_overlay_main';
  static const overlayPortName = 'lt_overlay_ui';

  static ReceivePort? _port;

  /// Register the main isolate to receive messages from the overlay bubble.
  static void listenOnMain(void Function(dynamic message) onMessage) {
    _closeLocal();
    final port = ReceivePort();
    _port = port;
    IsolateNameServer.removePortNameMapping(mainPortName);
    IsolateNameServer.registerPortWithName(port.sendPort, mainPortName);
    port.listen(onMessage);
  }

  /// Register the overlay isolate to receive caption/status updates from main.
  static void listenOnOverlay(void Function(dynamic message) onMessage) {
    _closeLocal();
    final port = ReceivePort();
    _port = port;
    IsolateNameServer.removePortNameMapping(overlayPortName);
    IsolateNameServer.registerPortWithName(port.sendPort, overlayPortName);
    port.listen(onMessage);
  }

  /// Returns true if a listener was registered and the message was sent.
  static bool sendToOverlay(dynamic message) {
    final port = IsolateNameServer.lookupPortByName(overlayPortName);
    if (port == null) return false;
    port.send(message);
    return true;
  }

  /// Returns true if a listener was registered and the message was sent.
  static bool sendToMain(dynamic message) {
    final port = IsolateNameServer.lookupPortByName(mainPortName);
    if (port == null) return false;
    port.send(message);
    return true;
  }

  static void dispose() {
    _closeLocal();
    IsolateNameServer.removePortNameMapping(mainPortName);
  }

  static void disposeOverlay() {
    _closeLocal();
    IsolateNameServer.removePortNameMapping(overlayPortName);
  }

  static void _closeLocal() {
    _port?.close();
    _port = null;
  }
}
