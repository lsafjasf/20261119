import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const Core = require("../js/synth-core.js");
const sampleRate = 48000;

test("tracks target fundamentals within three cents across the playable range", () => {
  for (const midi of [40, 57, 64, 69, 76]) {
    const target = Core.midiToFrequency(midi);
    const render = Core.renderNote({
      sampleRate,
      frequency: target,
      position: 0.2,
      damping: 0.4,
      hardness: 0.6,
      duration: 1.5,
      seed: 0x100 + midi
    });
    const tracked = Core.spectralPitchTrack(render.samples, sampleRate, target);
    assert.ok(Math.abs(tracked.cents) < 3, `${Core.midiToNoteName(midi)} error: ${tracked.cents} cents`);
  }
});

test("higher damping produces a shorter measured tail", () => {
  const low = Core.renderNote({
    sampleRate,
    frequency: 220,
    position: 0.2,
    damping: 0.18,
    hardness: 0.65,
    duration: 6,
    seed: 0x22
  });
  const high = Core.renderNote({
    sampleRate,
    frequency: 220,
    position: 0.2,
    damping: 0.78,
    hardness: 0.65,
    duration: 6,
    seed: 0x22
  });
  assert.ok(low.stats.t60 > high.stats.t60 * 2, `${low.stats.t60}s should exceed ${high.stats.t60}s`);
});

test("pluck position creates the predicted same-pitch harmonic notches", () => {
  const comparison = Core.comparePluckPoints({
    sampleRate,
    frequency: 220,
    damping: 0.4,
    hardness: 0.6,
    positionA: 0.2,
    positionB: 0.33,
    harmonicCount: 12
  });
  const third = comparison.harmonics.find((item) => item.harmonic === 3);
  const fifth = comparison.harmonics.find((item) => item.harmonic === 5);
  assert.ok(third.dbB < third.dbA - 12, `H3 notch missing: ${third.dbA} / ${third.dbB}`);
  assert.ok(fifth.dbA < fifth.dbB - 4, `H5 notch missing: ${fifth.dbA} / ${fifth.dbB}`);
});

test("renders deterministically and becomes silent after the tail", () => {
  const params = {
    sampleRate,
    frequency: 196,
    position: 0.27,
    damping: 0.85,
    hardness: 0.48,
    duration: 4,
    seed: 0x9911
  };
  const first = Core.renderNote(params);
  const second = Core.renderNote(params);
  assert.deepEqual(first.samples.slice(0, 2048), second.samples.slice(0, 2048));
  assert.ok(first.stats.residualPeak < 1e-5, `residual peak ${first.stats.residualPeak}`);
  assert.ok(first.loop.loopGain < 1, `loop gain ${first.loop.loopGain}`);
});

test("event timeline renders to a valid mono 16-bit WAV", () => {
  const events = [
    { type: "pluck", time: 0, midi: 57, position: 0.2, damping: 0.5, hardness: 0.5, seed: 1 },
    { type: "pluck", time: 0.5, midi: 64, position: 0.3, damping: 0.6, hardness: 0.7, seed: 2 }
  ];
  const timeline = Core.renderTimeline(events, { sampleRate });
  const bytes = Core.encodeWav(timeline.samples, sampleRate);
  const header = new TextDecoder().decode(bytes.slice(0, 12));
  assert.equal(header.slice(0, 4), "RIFF");
  assert.equal(header.slice(8, 12), "WAVE");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  assert.equal(view.getUint16(22, true), 1);
  assert.equal(view.getUint32(24, true), sampleRate);
  assert.equal(view.getUint16(34, true), 16);
  assert.ok(timeline.duration > 2);
});
