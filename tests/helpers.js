import { createWavHeader } from "../src/wav.js";

export function makeFormat(overrides = {}) {
  const channels = overrides.channels ?? 2;
  const sampleRate = overrides.sampleRate ?? 48000;
  const bitsPerSample = overrides.bitsPerSample ?? 16;
  const bytesPerSample = bitsPerSample / 8;
  const blockAlign = channels * bytesPerSample;
  return {
    formatTag: 1,
    encoding: "integer",
    channels,
    sampleRate,
    byteRate: sampleRate * blockAlign,
    blockAlign,
    bitsPerSample,
    validBitsPerSample: bitsPerSample,
    bytesPerSample,
    ...overrides,
  };
}

export function encodeIntegerPcm(frames, format) {
  const output = new Uint8Array(frames.length * format.blockAlign);
  const view = new DataView(output.buffer);
  for (let frame = 0; frame < frames.length; frame += 1) {
    for (let channel = 0; channel < format.channels; channel += 1) {
      const offset = frame * format.blockAlign + channel * format.bytesPerSample;
      const value = frames[frame][channel];
      if (format.bitsPerSample === 16) {
        view.setInt16(offset, value, true);
      } else if (format.bitsPerSample === 24) {
        const unsigned = value < 0 ? value + 0x1000000 : value;
        view.setUint8(offset, unsigned & 0xff);
        view.setUint8(offset + 1, (unsigned >> 8) & 0xff);
        view.setUint8(offset + 2, (unsigned >> 16) & 0xff);
      } else if (format.bitsPerSample === 32) {
        view.setInt32(offset, value, true);
      } else if (format.bitsPerSample === 8) {
        view.setUint8(offset, value + 128);
      }
    }
  }
  return output;
}

export function makeWav(frames, format) {
  const pcm = encodeIntegerPcm(frames, format);
  const fullFormat = {
    ...format,
    dataLength: pcm.byteLength,
    frames: frames.length,
    duration: frames.length / format.sampleRate,
  };
  const header = createWavHeader(fullFormat, pcm.byteLength);
  const wav = new Uint8Array(header.byteLength + pcm.byteLength);
  wav.set(header);
  wav.set(pcm, header.byteLength);
  return { wav, pcm, format: { ...fullFormat, dataOffset: header.byteLength } };
}

export function readIntegerFrame(bytes, frame, format) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const values = [];
  for (let channel = 0; channel < format.channels; channel += 1) {
    const offset = frame * format.blockAlign + channel * format.bytesPerSample;
    if (format.bitsPerSample === 16) {
      values.push(view.getInt16(offset, true));
    } else if (format.bitsPerSample === 24) {
      let value = view.getUint8(offset) | (view.getUint8(offset + 1) << 8) | (view.getUint8(offset + 2) << 16);
      if (value & 0x800000) value -= 0x1000000;
      values.push(value);
    } else {
      values.push(view.getInt32(offset, true));
    }
  }
  return values;
}

