import 'dart:async';
import 'dart:typed_data';

import 'package:agora_rtc_engine/agora_rtc_engine.dart';

import '../../config/api_config.dart';
import '../../services/auth_service.dart';
import '../mic_translate/mic_translate_service.dart';

/// Service for in-app translated calls using Agora with custom audio (TTS as mic).
/// Other participants hear translated speech when user speaks.
class TranslatedCallService {
  TranslatedCallService({
    required this.targetLanguage,
    this.voiceId = '21m00Tcm4TlvDq8ikWAM',
  });

  final String targetLanguage;
  final String voiceId;

  RtcEngine? _engine;
  MediaEngine? _mediaEngine;
  int? _customTrackId;
  bool _inCall = false;
  MicTranslateService? _translateService;
  final _auth = AuthService();
  StreamSubscription<String>? _statusSub;

  final _statusController = StreamController<String>.broadcast();
  Stream<String> get statusStream => _statusController.stream;

  bool get inCall => _inCall;

  /// Join Agora channel with custom audio track; TTS will be pushed as mic.
  Future<bool> startTranslatedCall(String channelId) async {
    final appId = ApiConfig.agoraAppId;
    if (appId == null || appId.isEmpty) {
      _statusController.add('AGORA_APP_ID not configured. Set via --dart-define=AGORA_APP_ID=...');
      return false;
    }
    if (!await _auth.hasTokens()) {
      _statusController.add('Sign in required');
      return false;
    }

    final svc = MicTranslateService(
      targetLanguage: targetLanguage,
      voiceId: voiceId,
    );
    _translateService = svc;

    try {
      _statusController.add('Initializing Agora…');

      _engine = createAgoraRtcEngine();
      await _engine!.initialize(RtcEngineContext(appId: appId));

      _mediaEngine = _engine!.getMediaEngine();
      final config = const AudioTrackConfig(enableLocalPlayback: false);
      _customTrackId = await _mediaEngine!.createCustomAudioTrack(
        trackType: AudioTrackType.audioTrackDirect,
        config: config,
      );

      await _engine!.joinChannel(
        token: '',
        channelId: channelId,
        uid: 0,
        options: ChannelMediaOptions(
          channelProfile: ChannelProfileType.channelProfileCommunication,
          clientRoleType: ClientRoleType.clientRoleBroadcaster,
          publishMicrophoneTrack: false,
          publishCustomAudioTrack: true,
          publishCustomAudioTrackId: _customTrackId!,
        ),
      );

      _inCall = true;
      _statusController.add('In call — speak to translate');

      _statusSub = svc.statusStream.listen((s) {
        _statusController.add(s);
      });
      await svc.start();

      return true;
    } catch (e) {
      _statusController.add('Error: $e');
      return false;
    }
  }

  /// Push TTS PCM (16-bit) to Agora custom track. Called when TTS bytes are ready.
  Future<void> pushTtsPcm(List<int> pcmBytes) async {
    if (_mediaEngine == null || _customTrackId == null || !_inCall) return;
    try {
      final frame = AudioFrame(
        buffer: pcmBytes is Uint8List ? pcmBytes : Uint8List.fromList(pcmBytes),
        samplesPerSec: 24000,
        channels: 1,
        samplesPerChannel: pcmBytes.length ~/ 2,
        bytesPerSample: BytesPerSample.twoBytesPerSample,
      );
      await _mediaEngine!.pushAudioFrame(frame: frame, trackId: _customTrackId!);
    } catch (_) {}
  }

  Future<void> stop() async {
    _inCall = false;
    await _translateService?.stop();
    _statusSub?.cancel();
    if (_mediaEngine != null && _customTrackId != null) {
      await _mediaEngine!.destroyCustomAudioTrack(_customTrackId!);
    }
    if (_engine != null) {
      await _engine!.leaveChannel();
      await _engine!.release();
    }
    _engine = null;
    _mediaEngine = null;
    _customTrackId = null;
  }

  void dispose() {
    _statusController.close();
    _translateService?.dispose();
  }
}
