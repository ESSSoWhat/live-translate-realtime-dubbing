# Installer Size Reduction Recommendations

Current installer size: **~287 MB** (LiveTranslate-0.2.3-Setup.exe)

The large size is primarily due to PyTorch dependencies bundled in the installer. While this provides a complete offline experience, it creates friction for users downloading over slow connections or mobile hotspots.

## Recommended Options (from lowest to highest risk)

### 1. Use CPU-only PyTorch build (RECOMMENDED - ~100 MB savings)

**What**: Replace CUDA-enabled PyTorch with CPU-only wheels during the build.

**Benefit**: Removes CUDA DLLs (~100 MB) that aren't needed since the app only uses CPU inference for Silero VAD.

**Risk**: Low - the app already filters CUDA DLLs in `live_translate.spec` and only needs CPU.

**Implementation**:
```bash
pip uninstall torch torchaudio
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pyinstaller live_translate.spec
```

**Already documented** in the spec file (line 192-198) and `__main__.py` (line 243-253).

### 2. On-demand model download (MODERATE - saves Silero VAD model bundling)

**What**: Download Silero VAD models on first run instead of bundling them in the installer.

**Benefit**: Reduces installer by ~5-10 MB; allows model updates without reinstalling.

**Risk**: Moderate - requires internet on first run; adds complexity to error handling.

**Implementation**:
- Remove `silero_vad/data` from `live_translate.spec` datas
- Download from Silero's model hub on first app launch
- Cache in `%LOCALAPPDATA%/Live Translate/models/`

### 3. Slim bootstrapper installer (COMPLEX - significant redesign)

**What**: Create a small (~10 MB) bootstrapper that downloads the full app bundle on first run.

**Benefit**: Dramatically reduces initial download; allows delta updates.

**Risk**: High - requires complete installer redesign, hosting infrastructure for app bundles, fallback logic for offline installs.

**Implementation**:
- Small Inno Setup installer downloads full bundle from CDN
- Or use MSI with on-demand feature installation
- Requires hosting infrastructure (S3/CDN) for app bundles

**Not recommended** unless download abandonment is causing measurable revenue loss.

### 4. Switch to NSIS + UPX compression (SMALL GAIN - packaging change)

**What**: Replace Inno Setup with NSIS and enable UPX compression.

**Benefit**: Slightly better compression (~10-20 MB savings).

**Risk**: Moderate - UPX can trigger false positives in antivirus software; NSIS migration effort.

**Implementation**:
- Create NSIS script equivalent to `installer.iss`
- Test extensively with Windows Defender and major AV vendors

**Not recommended** - compression gains are minimal compared to removing CUDA DLLs.

## Immediate Action

**Start with option 1 (CPU-only PyTorch)** - it's documented, low-risk, and provides the most significant reduction.

The current build process already removes CUDA DLLs from the bundle (see `live_translate.spec` line 195-198), but using CPU-only wheels from the start prevents them from being included in the first place and reduces build time.

## Verification

After implementing size reduction:
1. Test on a clean Windows 10/11 VM without GPU/CUDA installed
2. Verify Silero VAD still works (voice activity detection)
3. Run through the full dubbing workflow (capture → STT → translate → TTS → playback)
4. Check installer size with `dir LiveTranslate-*-Setup.exe`

## Communication

Update website copy to reflect actual download size:
- Replace "Get Started in Seconds" if download time doesn't match
- Add "~180 MB download" or similar size indicator near the download button
- Consider adding "Requires Windows 10+ and ~500 MB disk space" to system requirements
