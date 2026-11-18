import assert from "node:assert/strict";
import test from "node:test";
import { GranularEngine } from "../src/granular-engine.js";
import { makeConstantToneSource } from "../src/dsp-utils.js";
import { runAllDiagnostics } from "../src/diagnostics.js";

const RATE = 48000;

test("impulse onset maps from source offset to output sample", () => {
  const engine = new GranularEngine({ sampleRate: RATE });
  const source = new Float32Array(RATE * 0.2);
  source[1000] = 0.8;
  engine.setSource(source);
  engine.configure({
    grainLengthSeconds: 0.1,
    emissionIntervalSeconds: 2,
    windowType: "rect",
    maxVoices: 2,
    masterGain: 1,
    clipEnabled: false
  });
  engine.seek(500 / RATE);
  engine.setFrozen(true);
  engine.play();

  const output = new Float32Array(RATE * 0.1);
  engine.process(output);
  const nonzero = [];
  for (let i = 0; i < output.length; i += 1) {
    if (Math.abs(output[i]) > 1e-6) nonzero.push(i);
  }
  assert.deepEqual(nonzero, [500], "the impulse should appear 500 frames after grain onset");
  assert.ok(Math.abs(output[500] - 0.8) < 1e-6);
});

test("50% Hann overlap reconstructs a constant signal at unity gain", () => {
  const engine = new GranularEngine({ sampleRate: RATE });
  const constant = new Float32Array(RATE).fill(0.5);
  engine.setSource(constant);
  engine.configure({
    grainLengthSeconds: 0.1,
    emissionIntervalSeconds: 0.05,
    windowType: "hann",
    maxVoices: 4,
    masterGain: 1,
    clipEnabled: false
  });
  engine.setFrozen(true);
  engine.play();

  const output = new Float32Array(RATE * 0.35);
  engine.process(output);
  for (let i = RATE * 0.1; i < output.length; i += 1) {
    assert.ok(Math.abs(output[i] - 0.5) < 1e-6, `ripple at frame ${i}: ${output[i]}`);
  }
  assert.equal(engine.getStatus().peakActive, 2);
});

test("density changes emission timing without changing grain length", () => {
  const engine = new GranularEngine({ sampleRate: RATE });
  engine.setSource(new Float32Array(RATE).fill(0.25));
  engine.configure({
    grainLengthSeconds: 0.08,
    emissionIntervalSeconds: 0.04,
    windowType: "hann",
    maxVoices: 128,
    clipEnabled: false
  });
  engine.setFrozen(true);
  engine.play();
  engine.process(new Float32Array(RATE * 0.04));

  const before = engine.getStatus();
  assert.ok(before.grains.length > 0);
  assert.ok(before.grains.every((grain) => grain.length === Math.round(RATE * 0.08)));

  engine.setEmissionIntervalSeconds(0.01);
  engine.process(new Float32Array(RATE * 0.04));
  const after = engine.getStatus();
  assert.equal(after.grainLengthSeconds, 0.08);
  assert.ok(after.grains.every((grain) => grain.length === Math.round(RATE * 0.08)));
  assert.ok(after.grains.length > before.grains.length, "higher density should overlap more grains");
});

test("voice cap rejects new grains, counts them, and never grows a backlog", () => {
  const engine = new GranularEngine({ sampleRate: RATE });
  engine.setSource(makeConstantToneSource(RATE, 0.5));
  engine.configure({
    grainLengthSeconds: 0.05,
    emissionIntervalSeconds: 0.003,
    windowType: "hann",
    maxVoices: 6,
    masterGain: 0.5
  });
  engine.play();
  const output = new Float32Array(RATE * 0.4);
  engine.process(output);
  const status = engine.getStatus();

  assert.equal(status.policy, "reject-new");
  assert.ok(status.peakActive <= 6);
  assert.ok(status.rejected > 0);
  assert.equal(status.queueLength, 0);
  assert.ok(status.grains.length <= 6);
  assert.equal(status.attempts, status.accepted + status.rejected);
});

test("freeze holds every new grain at the same source frame", () => {
  const engine = new GranularEngine({ sampleRate: RATE });
  engine.setSource(makeConstantToneSource(RATE, 0.4));
  engine.configure({ grainLengthSeconds: 0.03, emissionIntervalSeconds: 0.008, maxVoices: 32 });
  engine.seek(0.125);
  engine.setFrozen(true);
  engine.play();
  engine.process(new Float32Array(RATE * 0.05));
  const status = engine.getStatus();

  assert.equal(status.frozen, true);
  assert.ok(status.grains.length > 2);
  assert.ok(status.grains.every(
    (grain) => Math.abs(grain.sourceStartSeconds - 0.125) < 1e-6
  ));
  assert.ok(Math.abs(status.readPositionSeconds - 0.125) < 1e-6);
});

test("built-in diagnostic suite passes", () => {
  const results = runAllDiagnostics(RATE);
  for (const result of results) {
    assert.equal(result.passed, true, `${result.label}: ${result.detail}`);
  }
});
