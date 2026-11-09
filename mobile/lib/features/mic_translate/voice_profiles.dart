import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:shared_preferences/shared_preferences.dart';

import 'speaker_id.dart';

const int kMaxAutoProfiles = 8;

/// Premade Rachel — same fallback as desktop / [MicTranslateService.defaultVoiceId].
const String kFallbackVoiceId = '21m00Tcm4TlvDq8ikWAM';

/// Named speaker profile with optional TTS voice assignment.
class VoiceProfile {
  VoiceProfile({
    required this.id,
    required this.name,
    this.assignedVoiceId,
    this.embedding,
    DateTime? createdAt,
  }) : createdAt = createdAt ?? DateTime.now();

  final String id;
  String name;
  String? assignedVoiceId;
  List<double>? embedding;
  final DateTime createdAt;

  VoiceProfile copy() => VoiceProfile(
        id: id,
        name: name,
        assignedVoiceId: assignedVoiceId,
        embedding: embedding == null ? null : List<double>.from(embedding!),
        createdAt: createdAt,
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'assigned_voice_id': assignedVoiceId,
        'embedding': embedding,
        'created_at': createdAt.toIso8601String(),
      };

  factory VoiceProfile.fromJson(Map<String, dynamic> d) {
    final emb = d['embedding'];
    List<double>? embedding;
    if (emb is List) {
      embedding = emb.map((e) => (e as num).toDouble()).toList();
    }
    DateTime created;
    final raw = d['created_at'];
    if (raw is String) {
      created = DateTime.tryParse(raw) ?? DateTime.now();
    } else {
      created = DateTime.now();
    }
    return VoiceProfile(
      id: d['id'] as String,
      name: (d['name'] as String?) ?? 'Speaker',
      assignedVoiceId: d['assigned_voice_id'] as String?,
      embedding: embedding,
      createdAt: created,
    );
  }
}

/// Persists profiles in SharedPreferences (JSON), mirroring desktop file store.
class VoiceProfileStore {
  VoiceProfileStore();

  static const _kKey = 'voice_profiles_v1';

  SharedPreferences? _prefs;

  Future<void> init() async {
    _prefs ??= await SharedPreferences.getInstance();
  }

  SharedPreferences get _p {
    final p = _prefs;
    if (p == null) throw StateError('VoiceProfileStore.init() required');
    return p;
  }

  Map<String, dynamic> _read() {
    final raw = _p.getString(_kKey);
    if (raw == null || raw.isEmpty) {
      return {'profiles': <dynamic>[], 'default_profile_id': null};
    }
    try {
      final decoded = jsonDecode(raw);
      if (decoded is Map<String, dynamic>) return decoded;
      if (decoded is Map) return Map<String, dynamic>.from(decoded);
    } catch (_) {}
    return {'profiles': <dynamic>[], 'default_profile_id': null};
  }

  Future<void> _write(Map<String, dynamic> data) async {
    await _p.setString(_kKey, jsonEncode(data));
  }

  Future<List<VoiceProfile>> loadAll() async {
    final data = _read();
    final list = data['profiles'];
    if (list is! List) return [];
    final out = <VoiceProfile>[];
    for (final entry in list) {
      if (entry is Map<String, dynamic>) {
        out.add(VoiceProfile.fromJson(entry));
      } else if (entry is Map) {
        out.add(VoiceProfile.fromJson(Map<String, dynamic>.from(entry)));
      }
    }
    return out;
  }

  Future<void> save(VoiceProfile profile) async {
    final data = _read();
    final profiles = List<dynamic>.from(data['profiles'] as List? ?? []);
    profiles.removeWhere((p) => p is Map && p['id'] == profile.id);
    profiles.add(profile.toJson());
    data['profiles'] = profiles;
    await _write(data);
  }

  Future<void> delete(String profileId) async {
    final data = _read();
    final profiles = List<dynamic>.from(data['profiles'] as List? ?? []);
    profiles.removeWhere((p) => p is Map && p['id'] == profileId);
    data['profiles'] = profiles;
    if (data['default_profile_id'] == profileId) {
      data['default_profile_id'] = null;
    }
    await _write(data);
  }

  String? getDefaultProfileId() {
    final v = _read()['default_profile_id'];
    return v is String ? v : null;
  }

  Future<void> setDefaultProfileId(String? profileId) async {
    final data = _read();
    data['default_profile_id'] = profileId;
    await _write(data);
  }

  Future<void> clearVoiceAssignments(String voiceId) async {
    final data = _read();
    final profiles = List<dynamic>.from(data['profiles'] as List? ?? []);
    var changed = false;
    for (final entry in profiles) {
      if (entry is Map && entry['assigned_voice_id'] == voiceId) {
        entry['assigned_voice_id'] = null;
        changed = true;
      }
    }
    if (changed) {
      data['profiles'] = profiles;
      await _write(data);
    }
  }

  static String newId() {
    final hex = List.generate(
      12,
      (_) => math.Random().nextInt(16).toRadixString(16),
    ).join();
    return 'profile_$hex';
  }
}

/// In-memory profile manager + MFCC matching + TTS voice resolution.
class VoiceProfileManager {
  VoiceProfileManager({VoiceProfileStore? store})
      : _store = store ?? VoiceProfileStore();

  final VoiceProfileStore _store;
  final SpeakerIdentifier _speakerId = SpeakerIdentifier();
  final Map<String, VoiceProfile> _profiles = {};
  String? _defaultProfileId;
  String? _activeProfileId;

  final _changes = StreamController<void>.broadcast();

  /// Fires when profiles / default / active speaker change.
  Stream<void> get changes => _changes.stream;

  bool _ready = false;

  Future<void> init() async {
    if (_ready) return;
    await _store.init();
    final loaded = await _store.loadAll();
    _profiles.clear();
    _speakerId.clear();
    for (final p in loaded) {
      _profiles[p.id] = p;
      final emb = p.embedding;
      if (emb != null && emb.isNotEmpty) {
        _speakerId.registerEmbedding(p.id, emb);
      }
    }
    _defaultProfileId = _store.getDefaultProfileId();
    if (_defaultProfileId != null && !_profiles.containsKey(_defaultProfileId)) {
      _defaultProfileId = null;
    }
    _ready = true;
    _notify();
  }

  List<VoiceProfile> get profiles {
    final list = _profiles.values.map((p) => p.copy()).toList()
      ..sort((a, b) => a.createdAt.compareTo(b.createdAt));
    return list;
  }

  String? get defaultProfileId => _defaultProfileId;
  String? get activeProfileId => _activeProfileId;

  VoiceProfile? getProfile(String id) => _profiles[id]?.copy();

  VoiceProfile? getDefaultProfile() {
    final id = _defaultProfileId;
    if (id == null) return null;
    return _profiles[id]?.copy();
  }

  Future<bool> setDefaultProfile(String? profileId) async {
    if (profileId != null && !_profiles.containsKey(profileId)) return false;
    _defaultProfileId = profileId;
    await _store.setDefaultProfileId(profileId);
    _notify();
    return true;
  }

  Future<bool> setProfileVoice(String profileId, String? voiceId) async {
    final profile = _profiles[profileId];
    if (profile == null) return false;
    profile.assignedVoiceId = voiceId;
    await _store.save(profile);
    _notify();
    return true;
  }

  Future<bool> renameProfile(String profileId, String newName) async {
    final profile = _profiles[profileId];
    if (profile == null) return false;
    final name = newName.trim();
    if (name.isEmpty) return false;
    profile.name = name;
    await _store.save(profile);
    _notify();
    return true;
  }

  Future<bool> deleteProfile(String profileId) async {
    if (!_profiles.containsKey(profileId)) return false;
    _profiles.remove(profileId);
    _speakerId.unregisterSpeaker(profileId);
    if (_defaultProfileId == profileId) _defaultProfileId = null;
    if (_activeProfileId == profileId) _activeProfileId = null;
    await _store.delete(profileId);
    _notify();
    return true;
  }

  Future<void> onVoiceDeleted(String voiceId) async {
    await _store.clearVoiceAssignments(voiceId);
    for (final p in _profiles.values) {
      if (p.assignedVoiceId == voiceId) p.assignedVoiceId = null;
    }
    _notify();
  }

  /// Resolve ElevenLabs voice id for [audio], with [fallbackVoiceId] as Rachel/user default.
  String resolveVoiceIdForAudio(
    Float64List audio, {
    required String fallbackVoiceId,
    bool autoCreate = true,
  }) {
    final (profile, _) = resolveProfileForAudio(audio, autoCreate: autoCreate);
    return resolveTtsVoiceId(profile, fallbackVoiceId: fallbackVoiceId);
  }

  String resolveTtsVoiceId(
    VoiceProfile? profile, {
    required String fallbackVoiceId,
  }) {
    if (profile?.assignedVoiceId != null &&
        profile!.assignedVoiceId!.isNotEmpty) {
      return profile.assignedVoiceId!;
    }
    final def = getDefaultProfile();
    if (def?.assignedVoiceId != null && def!.assignedVoiceId!.isNotEmpty) {
      return def.assignedVoiceId!;
    }
    return fallbackVoiceId.isNotEmpty ? fallbackVoiceId : kFallbackVoiceId;
  }

  /// Match audio to a profile; optionally auto-create when unmatched.
  (VoiceProfile?, double) resolveProfileForAudio(
    Float64List audio, {
    bool autoCreate = true,
  }) {
    final (profileId, confidence) = _speakerId.identify(audio);
    if (profileId != null && _profiles.containsKey(profileId)) {
      final profile = _profiles[profileId]!;
      _activeProfileId = profile.id;
      _notify();
      return (profile.copy(), confidence);
    }

    if (autoCreate && audio.length >= _speakerId.sampleRate * 0.5) {
      final created = _maybeAutoCreateProfile(audio);
      if (created != null) {
        _activeProfileId = created.id;
        _notify();
        return (created.copy(), 0.0);
      }
    }

    final def = getDefaultProfile();
    _activeProfileId = def?.id;
    _notify();
    return (def, confidence);
  }

  VoiceProfile? _maybeAutoCreateProfile(Float64List audio) {
    if (_profiles.length >= kMaxAutoProfiles) return null;

    final nextNum = _nextSpeakerNumber();
    final profile = VoiceProfile(
      id: VoiceProfileStore.newId(),
      name: 'Speaker $nextNum',
    );
    final emb = _speakerId.registerSpeaker(profile.id, audio);
    if (emb == null) return null;
    profile.embedding = emb.toList();
    _profiles[profile.id] = profile;
    if (_defaultProfileId == null) {
      _defaultProfileId = profile.id;
      unawaited(_store.setDefaultProfileId(profile.id));
    }
    unawaited(_store.save(profile));
    return profile;
  }

  int _nextSpeakerNumber() {
    final used = <int>{};
    for (final p in _profiles.values) {
      if (p.name.startsWith('Speaker ')) {
        final suffix = p.name.substring('Speaker '.length).trim();
        final n = int.tryParse(suffix);
        if (n != null) used.add(n);
      }
    }
    var n = 1;
    while (used.contains(n)) {
      n++;
    }
    return n;
  }

  void _notify() {
    if (!_changes.isClosed) _changes.add(null);
  }

  Future<void> dispose() async {
    await _changes.close();
  }
}
