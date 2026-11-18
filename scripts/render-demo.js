import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { makeConstantToneSource } from "../src/dsp-utils.js";
import { pcmToStereoWav, renderGranularPcm } from "../src/offline-render.js";

const sampleRate = 48000;
const outputPath = resolve(process.argv[2] || "artifacts/grain-field-demo.wav");
const source = makeConstantToneSource(sampleRate, 3, 220);
const result = await renderGranularPcm({
  source,
  sampleRate,
  durationSeconds: 3,
  settings: {
    grainLengthSeconds: 0.12,
    emissionIntervalSeconds: 0.04,
    windowType: "hann",
    maxVoices: 12,
    masterGain: 0.8,
    scanRate: 0,
    frozen: true,
    readPositionSeconds: 0.5
  }
});

await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, Buffer.from(pcmToStereoWav(result.pcm, sampleRate)));
console.log(`Wrote ${outputPath}`);
console.log(`accepted=${result.status.accepted} rejected=${result.status.rejected} peak=${result.status.peakActive}`);
