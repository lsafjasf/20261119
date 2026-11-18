import assert from 'node:assert/strict';
import test from 'node:test';
import {
  analyzeSweep,
  buildKnownFIR,
  createLogSweep,
  fft,
  linearConvolve,
} from '../src/dsp.js';

test('FFT and linear convolution agree with a direct result', () => {
  const real = Float64Array.from([1, -2, 3, 0.5, 0, -1, 2, 0]);
  const original = real.slice();
  const imag = new Float64Array(real.length);
  fft(real, imag);
  fft(real, imag, true);
  for (let i = 0; i < real.length; i++) {
    assert.ok(Math.abs(real[i] - original[i]) < 1e-10);
    assert.ok(Math.abs(imag[i]) < 1e-10);
  }

  const convolution = linearConvolve(
    Float64Array.from([1, 2, 3]),
    Float64Array.from([0.5, -1]),
  );
  assert.deepEqual(Array.from(convolution).map((value) => Math.round(value * 1000) / 1000), [0.5, 0, -0.5, -3]);
});

test('log sweep is causal with the requested frequency span and duration', () => {
  const sweep = createLogSweep({ f1: 100, f2: 12000, duration: 0.8 });
  assert.equal(sweep.samples.length, 38400);
  assert.equal(sweep.samples[0], 0);
  assert.ok(Math.abs(sweep.frequency[0] - 100) < 1e-9);
  assert.ok(Math.abs(sweep.frequency.at(-1) - 12000) < 2);
  assert.ok(sweep.timeBandwidth > 5);
});

test('known FIR exposes the direct tap and span', () => {
  const fir = buildKnownFIR('studio');
  assert.equal(fir.taps[0].kind, 'direct');
  assert.equal(fir.taps[0].index, Math.round(0.0055 * 48000));
  assert.equal(fir.samples[fir.directIndex], 1);
  assert.ok(fir.spanMs > 60);
});

test('linear analysis recovers the FIR peak delay and magnitude response', () => {
  const result = analyzeSweep({
    f1: 80,
    f2: 20000,
    duration: 1.2,
    presetId: 'studio',
    nonlinear: false,
  });

  assert.ok(Math.abs(result.metrics.peakDelayErrorMs) < 0.02);
  assert.ok(result.metrics.magnitudeRmsDb < 2.2);
  assert.ok(result.metrics.magnitudeMaxErrorDb < 4);
  assert.ok(result.metrics.h2Energy < 1e-20);
  assert.equal(result.mainPeak.delayMs, result.fir.directTimeMs);
});

test('quadratic distortion forms a separate H2 window and does not enter the room gate', () => {
  const result = analyzeSweep({
    f1: 80,
    f2: 20000,
    duration: 1.2,
    presetId: 'studio',
    nonlinear: true,
    k2: 0.08,
  });

  assert.ok(result.metrics.h2RelativeDb < -20);
  assert.ok(result.metrics.h2RelativeDb > -35);
  assert.ok(result.metrics.separationMarginMs > 20);
  assert.ok(result.h2.window.end < result.roomGate.start);
  assert.ok(result.h2.peakDelayMs < -100);
  assert.ok(result.metrics.h2LeakagePeakDb < -25);
  assert.ok(result.metrics.linearLeakagePeakDb < -35);
  assert.ok(Math.abs(result.metrics.peakDelayErrorMs) < 0.02);
  assert.ok(result.metrics.magnitudeRmsDb < 2.2);
});

test('deconvolution gain calibration holds when the sweep bandwidth changes', () => {
  const result = analyzeSweep({
    f1: 80,
    f2: 8000,
    duration: 1.2,
    presetId: 'studio',
    nonlinear: false,
  });
  assert.ok(result.metrics.magnitudeRmsDb < 0.5);
  assert.ok(Math.abs(result.metrics.peakDelayErrorMs) < 0.02);
});

test('H2 energy follows the squared nonlinear coefficient', () => {
  const low = analyzeSweep({
    duration: 0.9,
    presetId: 'studio',
    nonlinear: true,
    k2: 0.025,
  });
  const high = analyzeSweep({
    duration: 0.9,
    presetId: 'studio',
    nonlinear: true,
    k2: 0.1,
  });
  const expectedDb = 20 * Math.log10(0.1 / 0.025);
  const measuredDb = high.metrics.h2RelativeDb - low.metrics.h2RelativeDb;
  assert.ok(Math.abs(measuredDb - expectedDb) < 0.3);
});
