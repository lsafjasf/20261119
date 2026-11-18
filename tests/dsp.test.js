import test from 'node:test';
import assert from 'node:assert/strict';
import {
  angularDistance,
  audioRms,
  beamformAtAngle,
  diagnoseChannelMap,
  encodeWav,
  findPeaks,
  isUniformRing,
  makeCircularArray,
  scanBeam,
  swapChannels,
  synthesizeArray
} from '../js/dsp.js';

const sampleRate = 16000;
const speedOfSound = 343;

function makeScene(sources, reverb = 0.35) {
  const array = makeCircularArray(8, 0.12);
  const simulation = synthesizeArray({
    array,
    sources,
    speedOfSound,
    sampleRate,
    duration: 1.8,
    reverb,
    ambient: 0.025
  });
  const scan = scanBeam({ channels: simulation.channels, array, speedOfSound, sampleRate, stepDeg: 2 });
  return { array, simulation, scan, peaks: findPeaks(scan, { maxPeaks: 16 }) };
}

test('uniform circular array has expected geometry', () => {
  const array = makeCircularArray(8, 0.12);
  assert.equal(array.length, 8);
  assert.ok(isUniformRing(array));
  array.forEach((mic) => {
    assert.ok(Math.abs(Math.hypot(mic.x, mic.y) - 0.12) < 1e-9);
  });
  assert.equal(angularDistance(358, 2), 4);
});

test('single source estimate survives reverb and interference', () => {
  const { peaks } = makeScene([{
    angleDeg: 37,
    level: 0.72,
    seed: 101
  }], 0.38);
  const top = peaks[0];
  assert.ok(top, 'expected at least one peak');
  assert.ok(angularDistance(top.angle, 37) < 4, `angle error was ${angularDistance(top.angle, 37)}`);
  assert.ok(top.confidence > 0.65, `confidence was ${top.confidence}`);
  assert.ok(top.contrastDb > 6, `contrast was ${top.contrastDb}`);
});

test('dual source scan resolves both azimuths', () => {
  const { peaks } = makeScene([
    { angleDeg: 23, level: 0.78, seed: 101 },
    { angleDeg: 138, level: 0.56, seed: 888 }
  ], 0.32);
  const first = peaks.reduce((best, peak) => (
    angularDistance(peak.angle, 23) < angularDistance(best.angle, 23) ? peak : best
  ));
  const second = peaks.reduce((best, peak) => (
    angularDistance(peak.angle, 138) < angularDistance(best.angle, 138) ? peak : best
  ));
  assert.ok(angularDistance(first.angle, 23) < 4, `source A error was ${angularDistance(first.angle, 23)}`);
  assert.ok(angularDistance(second.angle, 138) < 5, `source B error was ${angularDistance(second.angle, 138)}`);
  assert.notEqual(first.angle, second.angle);
  assert.ok(first.confidence > 0.48);
});

test('channel mapping diagnosis detects a data-only swap', () => {
  const { array, simulation, peaks } = makeScene([{
    angleDeg: 37,
    level: 0.72,
    seed: 101
  }], 0.28);
  const candidates = peaks.slice(0, 2).map((peak) => peak.angle);
  const correct = diagnoseChannelMap({
    channels: simulation.channels,
    array,
    speedOfSound,
    sampleRate,
    referenceAngle: peaks[0].angle,
    candidateAngles: candidates
  });
  assert.equal(correct.consistent, true);
  assert.equal(correct.suspectSwap, null);

  const swapped = swapChannels(simulation.channels, 0, 1);
  const swappedScan = scanBeam({ channels: swapped, array, speedOfSound, sampleRate, stepDeg: 2 });
  const swappedPeaks = findPeaks(swappedScan);
  const detected = diagnoseChannelMap({
    channels: swapped,
    array,
    speedOfSound,
    sampleRate,
    referenceAngle: swappedPeaks[0].angle,
    candidateAngles: swappedPeaks.slice(0, 2).map((peak) => peak.angle)
  });
  assert.equal(detected.consistent, false);
  assert.deepEqual(detected.suspectSwap, [0, 1]);
  assert.equal(detected.recommendedMapping, 'swap-1-2');
});

test('explicit channel and coordinate remap preserves the estimate', () => {
  const { array, simulation, peaks } = makeScene([{
    angleDeg: 63,
    level: 0.72,
    seed: 202
  }], 0.24);
  const remappedChannels = swapChannels(simulation.channels, 0, 1);
  const remappedArray = swapChannels(array, 0, 1);
  const remappedScan = scanBeam({
    channels: remappedChannels,
    array: remappedArray,
    speedOfSound,
    sampleRate,
    stepDeg: 2
  });
  const remappedPeaks = findPeaks(remappedScan);
  assert.ok(angularDistance(remappedPeaks[0].angle, peaks[0].angle) < 1.1);

  const diagnosis = diagnoseChannelMap({
    channels: remappedChannels,
    array: remappedArray,
    speedOfSound,
    sampleRate,
    referenceAngle: remappedPeaks[0].angle,
    candidateAngles: remappedPeaks.slice(0, 2).map((peak) => peak.angle)
  });
  assert.equal(diagnosis.consistent, true);
});

test('directional enhancement raises target energy', () => {
  const array = makeCircularArray(8, 0.12);
  const simulation = synthesizeArray({
    array,
    sources: [{ angleDeg: 37, level: 0.72, seed: 101 }],
    speedOfSound,
    sampleRate,
    duration: 1.2,
    reverb: 0,
    ambient: 0.012
  });
  const target = beamformAtAngle({
    channels: simulation.channels,
    array,
    angleDeg: 37,
    speedOfSound,
    sampleRate
  });
  const away = beamformAtAngle({
    channels: simulation.channels,
    array,
    angleDeg: 127,
    speedOfSound,
    sampleRate
  });
  assert.ok(audioRms(target) > audioRms(away) * 1.8);
});

test('WAV encoder writes a valid mono PCM header', () => {
  const samples = new Float32Array([0, 0.25, -0.25, 0.5]);
  const buffer = encodeWav(samples, sampleRate);
  const view = new DataView(buffer);
  const text = (offset, length) => String.fromCharCode(
    ...Array.from({ length }, (_, index) => view.getUint8(offset + index))
  );
  assert.equal(text(0, 4), 'RIFF');
  assert.equal(text(8, 4), 'WAVE');
  assert.equal(view.getUint32(24, true), sampleRate);
  assert.equal(view.getUint16(22, true), 1);
  assert.equal(view.getUint32(40, true), samples.length * 2);
});
