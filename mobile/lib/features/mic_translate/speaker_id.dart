import 'dart:math' as math;
import 'dart:typed_data';

/// MFCC-based speaker identification (mirrors desktop `SpeakerIdentifier`).
class SpeakerIdentifier {
  SpeakerIdentifier({
    this.sampleRate = 16000,
    this.nMfcc = 20,
    this.nMels = 40,
    this.similarityThreshold = 0.60,
  }) {
    _melFb = _makeMelFilterbank(nMels, _nFft, sampleRate);
  }

  final int sampleRate;
  final int nMfcc;
  final int nMels;
  final double similarityThreshold;

  static const int _nFft = 512;

  final Map<String, Float64List> _embeddings = {};
  late final List<Float64List> _melFb;

  void clear() => _embeddings.clear();

  /// Register (or update) a speaker embedding. Returns embedding or null if too short.
  Float64List? registerSpeaker(String speakerKey, Float64List audio) {
    if (audio.length < sampleRate * 0.5) return null;
    final emb = _computeEmbedding(audio);
    _embeddings[speakerKey] = emb;
    return emb;
  }

  /// Rehydrate a stored L2-normalised embedding.
  void registerEmbedding(String speakerKey, List<double> embedding) {
    final emb = Float64List.fromList(embedding);
    final norm = _l2Norm(emb);
    if (norm > 0) {
      for (var i = 0; i < emb.length; i++) {
        emb[i] /= norm;
      }
    }
    _embeddings[speakerKey] = emb;
  }

  Float64List? getEmbedding(String speakerKey) {
    final e = _embeddings[speakerKey];
    return e == null ? null : Float64List.fromList(e);
  }

  void unregisterSpeaker(String speakerKey) => _embeddings.remove(speakerKey);

  /// Best match `(speakerKey, confidence)` or `(null, score)`.
  (String?, double) identify(Float64List audio) {
    if (_embeddings.isEmpty || audio.length < sampleRate * 0.3) {
      return (null, 0.0);
    }
    final emb = _computeEmbedding(audio);
    String? bestId;
    var bestScore = -1.0;
    for (final entry in _embeddings.entries) {
      final score = _dot(emb, entry.value);
      if (score > bestScore) {
        bestScore = score;
        bestId = entry.key;
      }
    }
    if (bestScore >= similarityThreshold) {
      return (bestId, bestScore);
    }
    return (null, bestScore);
  }

  int get speakerCount => _embeddings.length;

  Float64List _computeEmbedding(Float64List audio) {
    // Pre-emphasis
    final emph = Float64List(audio.length);
    emph[0] = audio[0];
    for (var i = 1; i < audio.length; i++) {
      emph[i] = audio[i] - 0.97 * audio[i - 1];
    }

    const hop = 160; // 10 ms @ 16 kHz
    final nFrames = math.max(1, (emph.length - _nFft) ~/ hop);
    final window = Float64List(_nFft);
    for (var i = 0; i < _nFft; i++) {
      window[i] = 0.54 - 0.46 * math.cos(2 * math.pi * i / (_nFft - 1));
    }

    final nBins = _nFft ~/ 2 + 1;
    final mfccs = List.generate(nFrames, (_) => Float64List(nMfcc));

    final frame = Float64List(_nFft);
    final power = Float64List(nBins);
    final mel = Float64List(nMels);
    final logMel = Float64List(nMels);

    for (var f = 0; f < nFrames; f++) {
      final start = f * hop;
      for (var i = 0; i < _nFft; i++) {
        final idx = start + i;
        frame[i] = (idx < emph.length ? emph[idx] : 0.0) * window[i];
      }
      _realPowerSpectrum(frame, power);

      for (var m = 0; m < nMels; m++) {
        var sum = 0.0;
        final fb = _melFb[m];
        for (var k = 0; k < nBins; k++) {
          sum += power[k] * fb[k];
        }
        mel[m] = math.max(sum, 1e-10);
        logMel[m] = math.log(mel[m]);
      }

      // DCT-II ortho, skip c0 → take nMfcc coeffs
      final coeffs = _dctOrtho(logMel);
      for (var c = 0; c < nMfcc; c++) {
        mfccs[f][c] = coeffs[c + 1];
      }
    }

    final deltas = List.generate(nFrames, (_) => Float64List(nMfcc));
    for (var c = 0; c < nMfcc; c++) {
      deltas[0][c] = 0;
      for (var f = 1; f < nFrames; f++) {
        deltas[f][c] = mfccs[f][c] - mfccs[f - 1][c];
      }
    }

    final emb = Float64List(nMfcc * 4);
    _statPool(mfccs, emb, 0);
    _statPool(deltas, emb, nMfcc * 2);

    final norm = _l2Norm(emb);
    if (norm > 0) {
      for (var i = 0; i < emb.length; i++) {
        emb[i] /= norm;
      }
    }
    return emb;
  }

  void _statPool(List<Float64List> frames, Float64List out, int offset) {
    final n = frames.length;
    final dim = frames[0].length;
    for (var c = 0; c < dim; c++) {
      var sum = 0.0;
      for (final f in frames) {
        sum += f[c];
      }
      final mean = sum / n;
      var varSum = 0.0;
      for (final f in frames) {
        final d = f[c] - mean;
        varSum += d * d;
      }
      out[offset + c] = mean;
      out[offset + dim + c] = math.sqrt(varSum / n);
    }
  }

  /// In-place real FFT power spectrum via radix-2 Cooley–Tukey on zero-padded frame.
  void _realPowerSpectrum(Float64List frame, Float64List powerOut) {
    final n = frame.length;
    final re = Float64List.fromList(frame);
    final im = Float64List(n);
    _fft(re, im);
    final half = n ~/ 2;
    for (var k = 0; k <= half; k++) {
      final mag = math.sqrt(re[k] * re[k] + im[k] * im[k]);
      powerOut[k] = mag * mag;
    }
  }

  void _fft(Float64List re, Float64List im) {
    final n = re.length;
    // Bit-reversal
    var j = 0;
    for (var i = 1; i < n; i++) {
      var bit = n >> 1;
      for (; j & bit != 0; bit >>= 1) {
        j ^= bit;
      }
      j ^= bit;
      if (i < j) {
        final tr = re[i];
        re[i] = re[j];
        re[j] = tr;
        final ti = im[i];
        im[i] = im[j];
        im[j] = ti;
      }
    }
    for (var len = 2; len <= n; len <<= 1) {
      final ang = -2 * math.pi / len;
      final wlenRe = math.cos(ang);
      final wlenIm = math.sin(ang);
      for (var i = 0; i < n; i += len) {
        var wRe = 1.0;
        var wIm = 0.0;
        for (var k = 0; k < len ~/ 2; k++) {
          final uRe = re[i + k];
          final uIm = im[i + k];
          final vRe = re[i + k + len ~/ 2] * wRe - im[i + k + len ~/ 2] * wIm;
          final vIm = re[i + k + len ~/ 2] * wIm + im[i + k + len ~/ 2] * wRe;
          re[i + k] = uRe + vRe;
          im[i + k] = uIm + vIm;
          re[i + k + len ~/ 2] = uRe - vRe;
          im[i + k + len ~/ 2] = uIm - vIm;
          final nextWRe = wRe * wlenRe - wIm * wlenIm;
          wIm = wRe * wlenIm + wIm * wlenRe;
          wRe = nextWRe;
        }
      }
    }
  }

  /// DCT-II with ortho norm (scipy `dct(..., type=2, norm='ortho')`).
  Float64List _dctOrtho(Float64List x) {
    final n = x.length;
    final out = Float64List(n);
    final scale0 = math.sqrt(1.0 / n);
    final scale = math.sqrt(2.0 / n);
    for (var k = 0; k < n; k++) {
      var sum = 0.0;
      for (var i = 0; i < n; i++) {
        sum += x[i] * math.cos(math.pi / n * (i + 0.5) * k);
      }
      out[k] = sum * (k == 0 ? scale0 : scale);
    }
    return out;
  }

  static List<Float64List> _makeMelFilterbank(
    int nMels,
    int nFft,
    int sampleRate,
  ) {
    double hz2mel(double hz) => 2595.0 * math.log(1.0 + hz / 700.0) / math.ln10;
    double mel2hz(double m) => 700.0 * (math.pow(10.0, m / 2595.0) - 1.0);

    final lowMel = hz2mel(0);
    final highMel = hz2mel(sampleRate / 2);
    final melPts = List<double>.generate(
      nMels + 2,
      (i) => lowMel + (highMel - lowMel) * i / (nMels + 1),
    );
    final bins = melPts
        .map((m) => ((nFft + 1) * mel2hz(m) / sampleRate).floor())
        .toList();

    final nBins = nFft ~/ 2 + 1;
    final fb = List.generate(nMels, (_) => Float64List(nBins));
    for (var i = 0; i < nMels; i++) {
      final left = bins[i];
      final center = bins[i + 1];
      final right = bins[i + 2];
      for (var j = left; j < center; j++) {
        if (j < 0 || j >= nBins) continue;
        final denom = center - left;
        fb[i][j] = denom == 0 ? 0 : (j - left) / denom;
      }
      for (var j = center; j < right; j++) {
        if (j < 0 || j >= nBins) continue;
        final denom = right - center;
        fb[i][j] = denom == 0 ? 0 : (right - j) / denom;
      }
    }
    return fb;
  }

  static double _dot(Float64List a, Float64List b) {
    var s = 0.0;
    final n = math.min(a.length, b.length);
    for (var i = 0; i < n; i++) {
      s += a[i] * b[i];
    }
    return s;
  }

  static double _l2Norm(Float64List v) {
    var s = 0.0;
    for (final x in v) {
      s += x * x;
    }
    return math.sqrt(s);
  }
}
