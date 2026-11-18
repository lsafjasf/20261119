import assert from "node:assert/strict";
import test from "node:test";
import {
  DelayEngine,
  cubicTap,
  linearTap,
  MAX_FEEDBACK,
} from "../src/dsp.js";

function impulseCentroid(samples, start, end) {
  let weight = 0;
  let moment = 0;
  for (let index = start; index < end; index += 1) {
    const magnitude = Math.abs(samples[index]);
    weight += magnitude;
    moment += index * magnitude;
  }
  return weight > 0 ? moment / weight : 0;
}

test("linear and cubic taps reconstruct fractional positions", () => {
  const buffer = new Float32Array(16);
  buffer[3] = 1;
  assert.equal(linearTap(buffer, 15, 8, 4.75), 0.75);
  assert.equal(linearTap(buffer, 15, 8, 5), 1);

  const ramp = new Float32Array(16);
  ramp.forEach((_, index) => {
    ramp[index] = index;
  });
  assert.ok(Math.abs(cubicTap(ramp, 15, 10, 4.3) - 5.7) < 1e-12);
  assert.ok(Math.abs(cubicTap(ramp, 15, 10, 4) - 6) < 1e-12);
});

for (const interpolation of ["linear", "cubic"]) {
  test(`${interpolation} impulse centroid resolves the exact fractional delay`, () => {
    const engine = new DelayEngine({
      sampleRate: 48000,
      feedback: 0,
      mix: 1,
      interpolation,
    });
    engine.setDelayMs(17.35, { immediate: true });
    const delay = engine.effectiveDelaySamples;
    const impulseAt = 80;
    const total = impulseAt + Math.ceil(delay) + 96;
    const input = new Float32Array(total);
    const output = new Float32Array(total);
    input[impulseAt] = 1;
    engine.processBlock(input, output);

    const center = impulseAt + delay;
    const centroid = impulseCentroid(output, Math.floor(center) - 20, Math.ceil(center) + 21);
    assert.ok(
      Math.abs(centroid - center) < 1e-6,
      `centroid error: ${centroid - center} samples`,
    );
  });
}

test("dual heads crossfade with complementary gains and finish on the target", () => {
  const engine = new DelayEngine({
    sampleRate: 48000,
    feedback: 0,
    interpolation: "cubic",
    crossfadeMs: 10,
  });
  engine.setDelayMs(6, { immediate: true });
  const warmup = new Float32Array(1000).fill(0.08);
  const warmupOut = new Float32Array(warmup.length);
  engine.processBlock(warmup, warmupOut);

  engine.setDelayMs(34);
  assert.equal(engine.fading, true);
  assert.equal(engine.crossfadeSamples, 480);

  const firstHalf = new Float32Array(200).fill(0.08);
  const firstHalfOut = new Float32Array(firstHalf.length);
  engine.processBlock(firstHalf, firstHalfOut);
  const mid = engine.telemetry();
  assert.equal(mid.fading, true);
  assert.ok(Math.abs(mid.gainA + mid.gainB - 1) < 1e-12);
  assert.ok(mid.effectiveDelay > 6 * 48 && mid.effectiveDelay < 34 * 48);

  const secondHalf = new Float32Array(400).fill(0.08);
  const secondHalfOut = new Float32Array(secondHalf.length);
  engine.processBlock(secondHalf, secondHalfOut);
  const done = engine.telemetry();
  assert.equal(done.fading, false);
  assert.ok(Math.abs(done.effectiveDelay - 34 * 48) < 1e-9);
  assert.ok([...firstHalfOut, ...secondHalfOut].every(Number.isFinite));
});

test("feedback at the upper limit remains bounded", () => {
  const sampleRate = 48000;
  const engine = new DelayEngine({
    sampleRate,
    feedback: MAX_FEEDBACK,
    mix: 1,
    interpolation: "cubic",
  });
  engine.setDelayMs(9.7, { immediate: true });
  engine.setFeedback(MAX_FEEDBACK);
  let maximum = 0;
  let nonFinite = 0;
  for (let index = 0; index < sampleRate * 5; index += 1) {
    const input = Math.sin((2 * Math.PI * 410 * index) / sampleRate) * 0.32;
    const output = engine.processSample(input);
    if (!Number.isFinite(output)) nonFinite += 1;
    maximum = Math.max(maximum, Math.abs(output));
  }
  assert.equal(nonFinite, 0);
  assert.ok(maximum <= 1.2000001, `output escaped limiter: ${maximum}`);
  assert.ok(engine.telemetry().nonFiniteResets === 0);
});

test("paused blocks are not consumed and resume starts from a flushed buffer", () => {
  const engine = new DelayEngine({
    sampleRate: 48000,
    feedback: 0,
    interpolation: "linear",
  });
  engine.setDelayMs(5, { immediate: true });
  const before = engine.writePosition;
  const pausedInput = new Float32Array(128).fill(0.5);
  const pausedOutput = new Float32Array(128).fill(0.25);
  const processed = engine.processBlock(pausedInput, pausedOutput, true);
  assert.equal(processed, false);
  assert.equal(engine.writePosition, before);
  assert.ok(pausedOutput.every((sample) => sample === 0));

  engine.flush();
  const resumedInput = new Float32Array(400).fill(0.1);
  const resumedOutput = new Float32Array(400);
  engine.processBlock(resumedInput, resumedOutput, false);
  const delaySamples = Math.ceil(engine.effectiveDelaySamples);
  assert.ok(resumedOutput.subarray(0, delaySamples - 1).every((sample) => sample === 0));
  assert.equal(engine.writePosition, 400);
});
