export function formatLabel(format) {
  const kind = format.encoding === "float" ? "float" : "int";
  return `${format.bitsPerSample}-bit ${kind} · ${format.sampleRate.toLocaleString()} Hz · ${format.channels} ch`;
}

export function decodePlanar(bytes, format, frameCount = null) {
  const availableFrames = Math.floor(bytes.byteLength / format.blockAlign);
  const frames = frameCount == null ? availableFrames : Math.min(frameCount, availableFrames);
  if (frames < 0) {
    throw new RangeError("Frame count cannot be negative.");
  }

  const channels = format.channels;
  const planar = Array.from({ length: channels }, () => new Float32Array(frames));
  const bytesPerSample = format.bytesPerSample;
  const floatView =
    format.encoding === "float"
      ? new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
      : null;

  for (let frame = 0; frame < frames; frame += 1) {
    const frameOffset = frame * format.blockAlign;
    for (let channel = 0; channel < channels; channel += 1) {
      const offset = frameOffset + channel * bytesPerSample;
      let sample;

      if (format.encoding === "float") {
        sample =
          format.bitsPerSample === 32
            ? floatView.getFloat32(offset, true)
            : floatView.getFloat64(offset, true);
      } else if (format.bitsPerSample === 8) {
        sample = (bytes[offset] - 128) / 128;
      } else if (format.bitsPerSample === 16) {
        const value = (bytes[offset] | (bytes[offset + 1] << 8)) << 16 >> 16;
        sample = value / 0x8000;
      } else if (format.bitsPerSample === 24) {
        let value = bytes[offset] | (bytes[offset + 1] << 8) | (bytes[offset + 2] << 16);
        if (value & 0x800000) {
          value |= 0xff000000;
        }
        sample = value / 0x800000;
      } else {
        const value =
          bytes[offset] |
          (bytes[offset + 1] << 8) |
          (bytes[offset + 2] << 16) |
          (bytes[offset + 3] << 24);
        sample = value / 0x80000000;
      }
      planar[channel][frame] = sample;
    }
  }

  return planar;
}

export function copyPlanarToAudioBuffer(audioBuffer, planar, frameCount) {
  for (let channel = 0; channel < planar.length; channel += 1) {
    audioBuffer.copyToChannel(planar[channel].subarray(0, frameCount), channel);
  }
}

