import { decodePlanar } from "./pcm.js";
import { readExact } from "./wav.js";

export async function buildWaveform(source, format, options = {}) {
  const buckets = Math.max(1, Math.floor(options.buckets || 2000));
  const blockFrames = Math.max(1, Math.floor(options.blockFrames || 32768));
  const totalFrames = format.frames;
  const channels = format.channels;
  const minima = Array.from({ length: channels }, () => new Float32Array(buckets));
  const maxima = Array.from({ length: channels }, () => new Float32Array(buckets));
  for (const channel of minima) channel.fill(1);
  for (const channel of maxima) channel.fill(-1);

  if (totalFrames === 0) {
    return { buckets, frames: totalFrames, channels, minima, maxima };
  }

  for (let startFrame = 0; startFrame < totalFrames; startFrame += blockFrames) {
    if (options.signal?.aborted) {
      throw new DOMException("Waveform scan aborted.", "AbortError");
    }
    const frameCount = Math.min(blockFrames, totalFrames - startFrame);
    const offset = format.dataOffset + startFrame * format.blockAlign;
    const bytes = await readExact(source, offset, frameCount * format.blockAlign);
    const planar = decodePlanar(bytes, format, frameCount);

    for (let channel = 0; channel < channels; channel += 1) {
      const samples = planar[channel];
      for (let frame = 0; frame < frameCount; frame += 1) {
        const bucket = Math.min(
          buckets - 1,
          Math.floor(((startFrame + frame) * buckets) / totalFrames),
        );
        const value = samples[frame];
        if (value < minima[channel][bucket]) minima[channel][bucket] = value;
        if (value > maxima[channel][bucket]) maxima[channel][bucket] = value;
      }
    }

    if (options.onProgress) {
      options.onProgress(Math.min(1, (startFrame + frameCount) / totalFrames));
    }
    await new Promise((resolve) => setTimeout(resolve, 0));
  }

  for (let channel = 0; channel < channels; channel += 1) {
    for (let bucket = 0; bucket < buckets; bucket += 1) {
      if (minima[channel][bucket] === 1 && maxima[channel][bucket] === -1) {
        minima[channel][bucket] = 0;
        maxima[channel][bucket] = 0;
      }
    }
  }

  return { buckets, frames: totalFrames, channels, minima, maxima };
}

