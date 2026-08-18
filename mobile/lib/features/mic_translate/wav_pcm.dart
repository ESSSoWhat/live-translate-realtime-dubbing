import 'dart:typed_data';

/// Decode mono PCM16 WAV (or raw PCM16) to float samples in [-1, 1].
///
/// Returns null when the buffer is empty or unreadable.
Float64List? decodeWavToFloats(List<int> bytes, {int sampleRate = 16000}) {
  if (bytes.isEmpty) return null;
  final data = bytes is Uint8List ? bytes : Uint8List.fromList(bytes);

  var offset = 0;
  var channels = 1;
  var rate = sampleRate;
  var bitsPerSample = 16;

  if (data.length >= 12 &&
      data[0] == 0x52 &&
      data[1] == 0x49 &&
      data[2] == 0x46 &&
      data[3] == 0x46) {
    // RIFF WAVE
    offset = 12;
    var foundData = false;
    while (offset + 8 <= data.length) {
      final id = String.fromCharCodes(data.sublist(offset, offset + 4));
      final size = ByteData.sublistView(data, offset + 4, offset + 8)
          .getUint32(0, Endian.little);
      offset += 8;
      if (id == 'fmt ' && size >= 16 && offset + 16 <= data.length) {
        final bd = ByteData.sublistView(data, offset, offset + 16);
        channels = bd.getUint16(2, Endian.little);
        rate = bd.getUint32(4, Endian.little);
        bitsPerSample = bd.getUint16(14, Endian.little);
      } else if (id == 'data') {
        foundData = true;
        break;
      }
      offset += size + (size.isOdd ? 1 : 0);
    }
    if (!foundData || bitsPerSample != 16) return null;
  }

  final pcm = data.sublist(offset);
  if (pcm.length < 2) return null;

  final sampleCount = pcm.length ~/ 2;
  final out = Float64List(channels <= 1 ? sampleCount : sampleCount ~/ channels);
  final bd = ByteData.sublistView(pcm);
  if (channels <= 1) {
    for (var i = 0; i < out.length; i++) {
      out[i] = bd.getInt16(i * 2, Endian.little) / 32768.0;
    }
  } else {
    // Downmix to mono (first channel).
    var j = 0;
    for (var i = 0; i + channels <= sampleCount; i += channels) {
      out[j++] = bd.getInt16(i * 2, Endian.little) / 32768.0;
    }
  }

  // Resample only if wildly off; live capture is already 16 kHz.
  if (rate != sampleRate && rate > 0 && out.isNotEmpty) {
    return _resampleLinear(out, rate, sampleRate);
  }
  return out;
}

Float64List _resampleLinear(Float64List input, int fromRate, int toRate) {
  final outLen = (input.length * toRate / fromRate).floor();
  if (outLen <= 0) return Float64List(0);
  final out = Float64List(outLen);
  final ratio = fromRate / toRate;
  for (var i = 0; i < outLen; i++) {
    final src = i * ratio;
    final i0 = src.floor();
    final i1 = (i0 + 1).clamp(0, input.length - 1);
    final t = src - i0;
    out[i] = input[i0] * (1 - t) + input[i1] * t;
  }
  return out;
}
