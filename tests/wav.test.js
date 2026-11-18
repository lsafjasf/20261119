import assert from "node:assert/strict";
import test from "node:test";
import { bytesSource, parseWav } from "../src/wav.js";
import { makeFormat, makeWav } from "./helpers.js";

test("WAV parser reads interleaved 24-bit PCM metadata without loading audio", async () => {
  const format = makeFormat({
    channels: 4,
    sampleRate: 44100,
    bitsPerSample: 24,
    bytesPerSample: 3,
  });
  const frames = Array.from({ length: 13 }, (_, frame) =>
    Array.from({ length: 4 }, (_, channel) => frame * 100 + channel),
  );
  const { wav } = makeWav(frames, format);
  let readCalls = 0;
  const source = bytesSource(wav);
  const tracked = {
    ...source,
    async read(offset, length) {
      readCalls += 1;
      return source.read(offset, length);
    },
  };
  const parsed = await parseWav(tracked);
  assert.equal(parsed.channels, 4);
  assert.equal(parsed.sampleRate, 44100);
  assert.equal(parsed.bitsPerSample, 24);
  assert.equal(parsed.blockAlign, 12);
  assert.equal(parsed.frames, 13);
  assert.ok(readCalls < 10);
});

test("WAV parser rejects compressed or malformed input", async () => {
  const bytes = new Uint8Array(44);
  bytes.set([0x52, 0x49, 0x46, 0x46], 0);
  bytes.set([0x57, 0x41, 0x56, 0x45], 8);
  bytes.set([0x66, 0x6d, 0x74, 0x20], 12);
  bytes.set([16, 0, 0, 0], 16);
  bytes.set([6, 0], 20);
  await assert.rejects(parseWav(bytesSource(bytes)), /not integer PCM/);
});

