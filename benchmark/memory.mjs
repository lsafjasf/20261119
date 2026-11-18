import { createHash } from "node:crypto";
import { BufferMeter } from "../src/meter.js";
import { streamEffectPcm } from "../src/reverse.js";

const channels = 2;
const sampleRate = 96000;
const blockFrames = 32768;
const prefetchBlocks = 2;
const frames = Math.floor((256 * 1024 * 1024) / (channels * 2));
const format = {
  channels,
  sampleRate,
  bitsPerSample: 16,
  bytesPerSample: 2,
  blockAlign: channels * 2,
  byteRate: sampleRate * channels * 2,
  encoding: "integer",
  dataOffset: 44,
  dataLength: frames * channels * 2,
  frames,
};

let maxReadBytes = 0;
let readBytes = 0;
let outputBytes = 0;
let blocks = 0;
let arrayBufferPeak = process.memoryUsage().arrayBuffers;
const meter = new BufferMeter();
const digest = createHash("sha256");
const source = {
  size: format.dataOffset + format.dataLength,
  async read(offset, length) {
    maxReadBytes = Math.max(maxReadBytes, length);
    readBytes += length;
    const bytes = new Uint8Array(length);
    for (let index = 0; index < length; index += 1) {
      bytes[index] = ((offset + index) * 29 + 11) & 0xff;
    }
    return bytes;
  },
};

const sampler = setInterval(() => {
  arrayBufferPeak = Math.max(arrayBufferPeak, process.memoryUsage().arrayBuffers);
}, 4);
const started = performance.now();

for await (const chunk of streamEffectPcm(source, format, {
  blockFrames,
  prefetchBlocks,
  meter,
})) {
  digest.update(chunk);
  outputBytes += chunk.byteLength;
  blocks += 1;
  if (blocks % 128 === 0) global.gc?.();
}

clearInterval(sampler);
global.gc?.();
const elapsed = performance.now() - started;
const usage = process.memoryUsage();
const peak = meter.snapshot();
const sourceMiB = format.dataLength / 1048576;

console.log(
  JSON.stringify(
    {
      sourceMiB: Number(sourceMiB.toFixed(1)),
      processedMiB: Number((outputBytes / 1048576).toFixed(1)),
      blockMiB: Number(((blockFrames * format.blockAlign) / 1048576).toFixed(3)),
      prefetchBlocks,
      maxReadBytes,
      trackedPeakBytes: peak.peakHeldBytes,
      maxHeldBlocks: peak.maxHeldBlocks,
      arrayBufferPeakMiB: Number((arrayBufferPeak / 1048576).toFixed(1)),
      finalRssMiB: Number((usage.rss / 1048576).toFixed(1)),
      elapsedMs: Math.round(elapsed),
      sha256: digest.digest("hex"),
    },
    null,
    2,
  ),
);

if (outputBytes !== format.dataLength) {
  throw new Error(`Output length mismatch: ${outputBytes} != ${format.dataLength}`);
}
if (readBytes !== format.dataLength) {
  throw new Error(`Read length mismatch: ${readBytes} != ${format.dataLength}`);
}
if (maxReadBytes > blockFrames * format.blockAlign) {
  throw new Error("Source read exceeded one block.");
}

