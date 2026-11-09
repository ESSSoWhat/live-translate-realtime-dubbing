import 'package:shared_preferences/shared_preferences.dart';

import '../features/mic_translate/mic_translate_service.dart';

/// Persisted mic-translate preferences (aligned with desktop settings surface).
class AppSettings {
  AppSettings._();

  static const _kVoiceId = 'voice_id';
  static const _kSourceLang = 'source_language';
  static const _kTargetLang = 'target_language';
  static const _kMuteDefault = 'mute_tts_default';
  static const _kTtsVolume = 'tts_volume';
  static const _kCaptionFontSize = 'caption_font_size';
  static const _kCaptionOpacity = 'caption_opacity';
  static const _kAutoClone = 'auto_clone_on_start';
  static const _kAutoCloneSeconds = 'auto_clone_seconds';
  static const _kTranslateMode = 'translate_mode';
  static const _kLiveCaptureMode = 'live_capture_mode';

  /// In-app mic captions only (no floating bubble).
  static const String modeMic = 'mic';

  /// Live Translate: app audio or screen OCR + floating overlay (Android).
  static const String modeLive = 'live';

  /// Live input: MediaProjection app/system playback → STT.
  static const String liveCaptureAudio = 'audio';

  /// Live input: MediaProjection screen frames → on-device OCR.
  static const String liveCaptureScreen = 'screen';

  static SharedPreferences? _prefs;

  static Future<void> init() async {
    _prefs ??= await SharedPreferences.getInstance();
  }

  static SharedPreferences get _p {
    final p = _prefs;
    if (p == null) {
      throw StateError('AppSettings.init() must be called before use');
    }
    return p;
  }

  static String get voiceId =>
      _p.getString(_kVoiceId) ?? MicTranslateService.defaultVoiceId;

  static Future<void> setVoiceId(String id) async {
    if (id.isEmpty) return;
    await _p.setString(_kVoiceId, id);
  }

  static String get sourceLanguage => _p.getString(_kSourceLang) ?? 'auto';

  static Future<void> setSourceLanguage(String code) async {
    await _p.setString(_kSourceLang, code);
  }

  static String get targetLanguage => _p.getString(_kTargetLang) ?? 'es';

  static Future<void> setTargetLanguage(String code) async {
    await _p.setString(_kTargetLang, code);
  }

  static bool get muteTtsDefault => _p.getBool(_kMuteDefault) ?? false;

  static Future<void> setMuteTtsDefault(bool value) async {
    await _p.setBool(_kMuteDefault, value);
  }

  /// 0.0–1.0
  static double get ttsVolume {
    final v = _p.getDouble(_kTtsVolume);
    if (v == null) return 1.0;
    return v.clamp(0.0, 1.0);
  }

  static Future<void> setTtsVolume(double value) async {
    await _p.setDouble(_kTtsVolume, value.clamp(0.0, 1.0));
  }

  static double get captionFontSize {
    final v = _p.getDouble(_kCaptionFontSize);
    if (v == null) return 14.0;
    return v.clamp(10.0, 28.0);
  }

  static Future<void> setCaptionFontSize(double value) async {
    await _p.setDouble(_kCaptionFontSize, value.clamp(10.0, 28.0));
  }

  /// 0.3–1.0 applied to caption text opacity.
  static double get captionOpacity {
    final v = _p.getDouble(_kCaptionOpacity);
    if (v == null) return 1.0;
    return v.clamp(0.3, 1.0);
  }

  static Future<void> setCaptionOpacity(double value) async {
    await _p.setDouble(_kCaptionOpacity, value.clamp(0.3, 1.0));
  }

  static bool get autoCloneOnStart => _p.getBool(_kAutoClone) ?? false;

  static Future<void> setAutoCloneOnStart(bool value) async {
    await _p.setBool(_kAutoClone, value);
  }

  static int get autoCloneSeconds {
    final v = _p.getInt(_kAutoCloneSeconds);
    if (v == null) return 5;
    return v.clamp(3, 15);
  }

  static Future<void> setAutoCloneSeconds(int value) async {
    await _p.setInt(_kAutoCloneSeconds, value.clamp(3, 15));
  }

  /// [modeMic] or [modeLive].
  static String get translateMode {
    final v = _p.getString(_kTranslateMode);
    if (v == modeLive) return modeLive;
    return modeMic;
  }

  static Future<void> setTranslateMode(String mode) async {
    if (mode != modeMic && mode != modeLive) return;
    await _p.setString(_kTranslateMode, mode);
  }

  /// [liveCaptureAudio] or [liveCaptureScreen] (Live Translate only).
  static String get liveCaptureMode {
    final v = _p.getString(_kLiveCaptureMode);
    if (v == liveCaptureScreen) return liveCaptureScreen;
    return liveCaptureAudio;
  }

  static Future<void> setLiveCaptureMode(String mode) async {
    if (mode != liveCaptureAudio && mode != liveCaptureScreen) return;
    await _p.setString(_kLiveCaptureMode, mode);
  }
}
