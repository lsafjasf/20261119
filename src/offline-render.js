import { GranularEngine } from "./granular-engine.js";
import { encodePcm16Wav } from "./wav.js";

export async function renderGranularPcm(options, onProgress = () => {}) {
  const {
    source,
    sampleRate,
    durationSeconds,
    settings = {}
  } = options;
  const frames = Math.max(1, Math.round(durationSeconds * sampleRate));
  const engine = new GranularEngine({ sampleRate });
  engine.setSource(source);
  engine.configure(settings);
  if (settings.readPositionSeconds !== undefined) engine.seek(settings.readPositionSeconds);
  if (settings.frozen !== undefined) engine.setFrozen(settings.frozen);
  engine.play();

  const pcm = new Float32Array(frames);
  const chunkFrames = Math.max(128, sampleRate >> 3);
  for (let offset = 0; offset < frames; offset += chunkFrames) {
    const end = Math.min(frames, offset + chunkFrames);
    engine.process(pcm.subarray(offset, end));
    onProgress(end / frames);
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  return {
    pcm,
    sampleRate,
    status: engine.getStatus()
  };
}

export function pcmToStereoWav(pcm, sampleRate) {
  return encodePcm16Wav([pcm, pcm], sampleRate);
}
