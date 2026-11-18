import { test } from "node:test";
import assert from "node:assert/strict";
import {
  HEADER_SIZE,
  decodeSegment,
  encodeSegment,
} from "../src/recovery/protocol.js";

const meta = { seq: 7, start: 7, duration: 1, bitrate: 500 };

test("encode/decode round trips segment metadata and frames", () => {
  const buffer = encodeSegment(meta, {
    frames: [{ pts: 7, key: true }, { pts: 7.25, key: false }],
  });
  assert.ok(buffer.length > HEADER_SIZE);
  const segment = decodeSegment(buffer);
  assert.equal(segment.seq, 7);
  assert.equal(segment.start, 7);
  assert.equal(segment.duration, 1);
  assert.equal(segment.bitrate, 500);
  assert.equal(segment.frames.length, 2);
});

test("tampered payload fails CRC check (simulated corrupt media)", () => {
  const buffer = encodeSegment(meta, { frames: [{ pts: 7, key: true }] });
  buffer[HEADER_SIZE] ^= 0xff;
  assert.throws(() => decodeSegment(buffer), /crc mismatch/);
});

test("truncated buffer fails magic check", () => {
  const buffer = encodeSegment(meta, { frames: [] });
  assert.throws(() => decodeSegment(buffer.subarray(0, 10)), /bad magic/);
});
