import test from "node:test";
import assert from "node:assert/strict";
import {
  analyzeBoundary,
  analyzeSpectrum,
  auditTenRounds,
  detectOnsets,
  measureLoop,
  mixToMono,
  prepareLoop,
  rms,
  spectralDistance
} from "../src/dsp.js";
import { createEndDrumClip, createPhaseOffsetTone } from "../src/signals.js";

const sampleRate = 48000;

test("phase-offset tone exposes a large raw seam discontinuity", () => {
  const source = createPhaseOffsetTone(sampleRate);
  const mono = mixToMono(source.channels);
  const boundary = analyzeBoundary(mono, sampleRate, 0, mono.length);

  assert.ok(boundary.sampleDelta > 0.5, "the synthetic seam should jump by more than 0.5 full scale");
  assert.ok(boundary.slopeJump > 0.01, "the seam should also show a derivative discontinuity");
});

test("crossfade renders a valid shorter loop without global attenuation", () => {
  const source = createEndDrumClip(sampleRate);
  const mono = mixToMono(source.channels);
  const fadeSamples = Math.round(sampleRate * 0.08);
  const loop = prepareLoop(source.channels, 0, mono.length, fadeSamples);
  const metrics = measureLoop(loop.channels, source.channels, 0, mono.length, sampleRate);

  assert.equal(loop.length, mono.length - fadeSamples);
  assert.ok(metrics.duration > 3.9);
  assert.ok(metrics.playable);
  assert.ok(metrics.peak > 0.4, "loop must remain audible");
  assert.ok(metrics.rmsRetention > 0.9, "crossfade must not behave like a global fade");
  assert.ok(metrics.stepExcess < 0.002, "seam step should not exceed the local sample-step envelope");
  assert.equal(metrics.globallyAttenuated, false);
});

test("ten-round audit reports finite seam values for every lap", () => {
  const source = createPhaseOffsetTone(sampleRate);
  const mono = mixToMono(source.channels);
  const fadeSamples = Math.round(sampleRate * 0.04);
  const loop = prepareLoop(source.channels, 0, mono.length, fadeSamples);
  const audit = auditTenRounds(loop.channels, source.channels, 0, mono.length, sampleRate);

  assert.equal(audit.length, 10);
  for (const reading of audit) {
    assert.ok(Number.isFinite(reading.stepExcess));
    assert.ok(Number.isFinite(reading.stepRatio));
    assert.ok(reading.stepRatio < 1.2);
    assert.ok(reading.rmsRetention > 0.95);
    assert.equal(reading.globallyAttenuated, false);
  }
});

test("crop and crossfade preserve stereo channel count", () => {
  const left = new Float32Array(4800).map((_, index) => Math.sin(index / 9));
  const right = new Float32Array(4800).map((_, index) => Math.sin(index / 11));
  const loop = prepareLoop([left, right], 120, 4560, 240);

  assert.equal(loop.channels.length, 2);
  assert.equal(loop.channels[0].length, 4200);
  assert.equal(loop.channels[1].length, 4200);
  assert.ok(loop.channels.every((channel) => channel.every(Number.isFinite)));
});

test("drum clip yields near-120 BPM and a transient close to the end", () => {
  const source = createEndDrumClip(sampleRate);
  const mono = mixToMono(source.channels);
  const beats = detectOnsets(mono, sampleRate);
  const nearestEnd = beats.onsets.reduce((nearest, onset) => {
    const distance = Math.abs(source.channels[0].length / sampleRate - onset.time);
    return !nearest || distance < nearest.distance ? { ...onset, distance } : nearest;
  }, null);

  assert.ok(Math.abs(beats.bpm - 120) < 2);
  assert.ok(beats.confidence > 0.8);
  assert.ok(nearestEnd.distance < 0.22);
});

test("FFT identifies the periodic tone and reports matching edge spectra", () => {
  const source = createPhaseOffsetTone(sampleRate);
  const mono = mixToMono(source.channels);
  const first = analyzeSpectrum(mono, sampleRate, 0);
  const last = analyzeSpectrum(mono, sampleRate, mono.length - 2048);
  const distance = spectralDistance(first, last);

  assert.ok(Math.abs(first.dominantFrequency - 220) < 12);
  assert.ok(distance < 0.05);
  assert.ok(rms(mono) > 0.3);
});
