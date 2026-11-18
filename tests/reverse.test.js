import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { BufferMeter } from "../src/meter.js";
import {
  collectStream,
  mapOutputFrameToSource,
  normalizeRegion,
  reversePcmExact,
  streamEffectPcm,
} from "../src/reverse.js";
import { bytesSource, parseWav } from "../src/wav.js";
import {
  makeFormat,
  makeWav,
  readIntegerFrame,
} from "./helpers.js";

function pulseFrames() {
  const left = new Map([
    [0, 97],
    [1, -311],
    [5, 1201],
    [11, -73],
    [12, 4096],
    [18, -777],
    [23, 53],
  ]);
  const right = new Map([
    [0, -17],
    [1, 902],
    [5, -1201],
    [11, 88],
    [12, -4096],
    [18, 221],
    [23, -900],
  ]);
  return Array.from({ length: 24 }, (_, frame) => [
    left.get(frame) || 0,
    right.get(frame) || 0,
  ]);
}

test("exact reverse preserves asymmetric stereo pulse pairing", () => {
  const format = makeFormat();
  const frames = pulseFrames();
  const pcm = new Uint8Array(frames.length * format.blockAlign);
  const view = new DataView(pcm.buffer);
  frames.forEach(([left, right], frame) => {
    view.setInt16(frame * 4, left, true);
    view.setInt16(frame * 4 + 2, right, true);
  });

  const reversed = reversePcmExact(pcm, { ...format, frames: frames.length }, {
    blockFrames: 5,
  });
  for (let frame = 0; frame < frames.length; frame += 1) {
    assert.deepEqual(
      readIntegerFrame(reversed, frame, format),
      frames[frames.length - 1 - frame],
    );
  }
});

test("cross-block continuous ramp has exact descending seams", () => {
  const format = makeFormat();
  const frames = Array.from({ length: 31 }, (_, frame) => [
    frame * 13,
    -frame * 13,
  ]);
  const normalizedFrames = frames.map(([left, right]) => [
    left === 0 ? 0 : left,
    right === 0 ? 0 : right,
  ]);
  const pcm = new Uint8Array(frames.length * format.blockAlign);
  const view = new DataView(pcm.buffer);
  frames.forEach(([left, right], frame) => {
    view.setInt16(frame * 4, left, true);
    view.setInt16(frame * 4 + 2, right, true);
  });

  const reversed = reversePcmExact(pcm, { ...format, frames: frames.length }, {
    blockFrames: 7,
  });
  let previous = null;
  for (let frame = 0; frame < frames.length; frame += 1) {
    const [left, right] = readIntegerFrame(reversed, frame, format);
    assert.equal(left, right === 0 ? 0 : -right);
    if (previous !== null) {
      const expected = previous - 13;
      assert.equal(
        left,
        expected === 0 ? 0 : expected,
        `seam mismatch before output frame ${frame}`,
      );
    }
    previous = left;
    assert.deepEqual([left, right], normalizedFrames[frames.length - 1 - frame]);
  }
});

test("streaming and exact paths agree for a bounded region", async () => {
  const format = makeFormat({ channels: 3 });
  const frames = Array.from({ length: 37 }, (_, frame) => [
    frame,
    -frame * 3,
    frame % 5,
  ]);
  const normalizedFrames = frames.map((frame) =>
    frame.map((sample) => (sample === 0 ? 0 : sample)),
  );
  const { wav, pcm } = makeWav(frames, format);
  const parsed = await parseWav(bytesSource(wav));
  const region = normalizeRegion({ startFrame: 6, endFrame: 29 }, frames.length);
  const expected = reversePcmExact(pcm, {
    ...format,
    frames: frames.length,
  }, { region, blockFrames: 6 });
  const actual = await collectStream(
    streamEffectPcm(bytesSource(wav), parsed, {
      region,
      blockFrames: 6,
      prefetchBlocks: 2,
    }),
  );

  assert.deepEqual(actual, expected);
  for (let frame = 0; frame < 6; frame += 1) {
    assert.deepEqual(readIntegerFrame(actual, frame, format), normalizedFrames[frame]);
  }
  for (let frame = 29; frame < 37; frame += 1) {
    assert.deepEqual(readIntegerFrame(actual, frame, format), normalizedFrames[frame]);
  }
});

test("two full reverses restore every frame at every PCM depth", () => {
  for (const bitsPerSample of [8, 16, 24, 32]) {
    const format = makeFormat({
      channels: 2,
      bitsPerSample,
      bytesPerSample: bitsPerSample / 8,
    });
    const range = 2 ** (bitsPerSample - 1);
    const frames = Array.from({ length: 101 }, (_, frame) => [
      ((frame * 977) % (range * 2)) - range,
      ((frame * 431) % (range * 2)) - range,
    ]);
    const { pcm } = makeWav(frames, format);
    const descriptor = { ...format, frames: frames.length };
    const once = reversePcmExact(pcm, descriptor, { blockFrames: 16 });
    const twice = reversePcmExact(once, descriptor, { blockFrames: 16 });
    assert.deepEqual(twice, pcm, `${bitsPerSample}-bit round trip changed bytes`);
  }
});

test("cursor mapping reports source frames for full and regional reverse", () => {
  const region = { startFrame: 4, endFrame: 10 };
  assert.equal(mapOutputFrameToSource(0, 12, { startFrame: 0, endFrame: 12 }), 11);
  assert.equal(mapOutputFrameToSource(11, 12, { startFrame: 0, endFrame: 12 }), 0);
  assert.equal(mapOutputFrameToSource(4, 12, region), 9);
  assert.equal(mapOutputFrameToSource(9, 12, region), 4);
  assert.equal(mapOutputFrameToSource(3, 12, region), 3);
  assert.equal(mapOutputFrameToSource(10, 12, region), 10);
});

test("block prefetch keeps peak held memory bounded", async () => {
  const format = makeFormat({ channels: 2, sampleRate: 96000 });
  const frames = 2_000_003;
  const dataOffset = 44;
  const dataLength = frames * format.blockAlign;
  const meter = new BufferMeter();
  const hash = createHash("sha256");
  let outputBytes = 0;
  let largestRequest = 0;
  let requestedBytes = 0;

  const syntheticSource = {
    size: dataOffset + dataLength,
    async read(offset, length) {
      largestRequest = Math.max(largestRequest, length);
      requestedBytes += length;
      outputBytes += 0;
      const bytes = new Uint8Array(length);
      for (let index = 0; index < length; index += 1) {
        bytes[index] = ((offset + index) * 31 + 17) & 0xff;
      }
      return bytes;
    },
  };

  const blockFrames = 8192;
  const prefetchBlocks = 2;
  for await (const chunk of streamEffectPcm(syntheticSource, {
    ...format,
    frames,
    dataOffset,
    dataLength,
  }, {
    blockFrames,
    prefetchBlocks,
    meter,
  })) {
    hash.update(chunk);
    outputBytes += chunk.byteLength;
  }

  const blockBytes = blockFrames * format.blockAlign;
  const peak = meter.snapshot();
  console.log(
    `memory test: source=${(dataLength / 1048576).toFixed(1)} MiB, ` +
      `peak=${(peak.peakHeldBytes / 1024).toFixed(0)} KiB, ` +
      `blocks=${peak.maxHeldBlocks}`,
  );
  assert.equal(outputBytes, dataLength);
  assert.equal(hash.digest("hex").length, 64);
  assert.ok(largestRequest <= blockBytes);
  assert.ok(peak.peakHeldBytes <= blockBytes * (prefetchBlocks + 1));
  assert.ok(peak.maxHeldBlocks <= prefetchBlocks + 1);
  assert.ok(peak.heldBytes === 0);
  assert.equal(requestedBytes, dataLength);
});

