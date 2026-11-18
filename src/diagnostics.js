import { GranularEngine } from "./granular-engine.js";
import { makeConstantToneSource } from "./dsp-utils.js";

function percentileRange(samples, start, end) {
  let min = Infinity;
  let max = -Infinity;
  for (let i = start; i < Math.min(end, samples.length); i += 1) {
    min = Math.min(min, samples[i]);
    max = Math.max(max, samples[i]);
  }
  return { min, max };
}

export function runImpulseDiagnostic(sampleRate = 48000) {
  const engine = new GranularEngine({ sampleRate });
  const source = new Float32Array(Math.round(sampleRate * 0.2));
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
  engine.seek(500 / sampleRate);
  engine.setFrozen(true);
  engine.play();

  const output = new Float32Array(Math.round(sampleRate * 0.1));
  engine.process(output);
  let onset = -1;
  let peak = 0;
  for (let i = 0; i < output.length; i += 1) {
    const magnitude = Math.abs(output[i]);
    if (onset < 0 && magnitude > 1e-6) onset = i;
    peak = Math.max(peak, magnitude);
  }
  const expectedOnset = 1000 - 500;
  const passed = onset === expectedOnset && Math.abs(peak - 0.8) < 1e-5;
  return {
    passed,
    label: "Impulse onset",
    detail: `source offset 500 → impulse 1000; output onset ${onset} (expected ${expectedOnset})`,
    metrics: { onset, expectedOnset, peak }
  };
}

export function runHannOverlapDiagnostic(sampleRate = 48000) {
  const engine = new GranularEngine({ sampleRate });
  const source = new Float32Array(Math.round(sampleRate * 0.5));
  source.fill(0.5);
  engine.setSource(source);
  engine.configure({
    grainLengthSeconds: 0.1,
    emissionIntervalSeconds: 0.05,
    windowType: "hann",
    maxVoices: 4,
    masterGain: 1,
    clipEnabled: false
  });
  engine.seek(0);
  engine.setFrozen(true);
  engine.play();

  const output = new Float32Array(Math.round(sampleRate * 0.4));
  engine.process(output);
  const start = Math.round(sampleRate * 0.1);
  const end = Math.round(sampleRate * 0.35);
  const range = percentileRange(output, start, end);
  const maxError = Math.max(Math.abs(range.min - 0.5), Math.abs(range.max - 0.5));
  return {
    passed: maxError < 1e-5,
    label: "Hann overlap · 50%",
    detail: `constant 0.5 → output ${range.min.toFixed(6)}…${range.max.toFixed(6)}`,
    metrics: { ...range, maxError }
  };
}

export function runConstantToneDiagnostic(sampleRate = 48000) {
  const engine = new GranularEngine({ sampleRate });
  const source = makeConstantToneSource(sampleRate, 0.6, 220);
  engine.setSource(source);
  engine.configure({
    grainLengthSeconds: 0.1,
    emissionIntervalSeconds: 0.05,
    windowType: "hann",
    maxVoices: 4,
    masterGain: 1,
    clipEnabled: false
  });
  engine.seek(0.1);
  engine.setFrozen(true);
  engine.play();

  const output = new Float32Array(Math.round(sampleRate * 0.4));
  engine.process(output);
  const start = Math.round(sampleRate * 0.15);
  const end = Math.round(sampleRate * 0.35);
  let sumSquares = 0;
  let peak = 0;
  for (let i = start; i < end; i += 1) {
    sumSquares += output[i] * output[i];
    peak = Math.max(peak, Math.abs(output[i]));
  }
  const rms = Math.sqrt(sumSquares / (end - start));
  const expectedRms = 0.5 / Math.SQRT2;
  const passed = Math.abs(rms - expectedRms) < 1e-4 && Math.abs(peak - 0.5) < 1e-4;
  return {
    passed,
    label: "Constant 220 Hz",
    detail: `50% Hann overlap → RMS ${rms.toFixed(5)}, peak ${peak.toFixed(5)}`,
    metrics: { rms, expectedRms, peak }
  };
}

export function runVoiceCapDiagnostic(sampleRate = 48000) {
  const engine = new GranularEngine({ sampleRate });
  engine.setSource(makeConstantToneSource(sampleRate, 0.5));
  engine.configure({
    grainLengthSeconds: 0.05,
    emissionIntervalSeconds: 0.003,
    windowType: "hann",
    maxVoices: 6,
    masterGain: 0.5
  });
  engine.play();

  const output = new Float32Array(Math.round(sampleRate * 0.4));
  engine.process(output);
  const status = engine.getStatus();
  const passed = status.peakActive <= status.maxVoices
    && status.rejected > 0
    && status.queueLength === 0
    && status.attempts === status.accepted + status.rejected;
  return {
    passed,
    label: "Voice cap · reject-new",
    detail: `peak ${status.peakActive}/${status.maxVoices}; rejected ${status.rejected}; backlog ${status.queueLength}`,
    metrics: status
  };
}

export function runFreezeDiagnostic(sampleRate = 48000) {
  const engine = new GranularEngine({ sampleRate });
  engine.setSource(makeConstantToneSource(sampleRate, 0.2));
  engine.configure({ grainLengthSeconds: 0.03, emissionIntervalSeconds: 0.01 });
  const frozenPosition = 0.125;
  engine.seek(frozenPosition);
  engine.setFrozen(true);
  engine.play();
  const output = new Float32Array(Math.round(sampleRate * 0.08));
  engine.process(output);
  const status = engine.getStatus();
  const allFrozen = status.grains.every(
    (grain) => Math.abs(grain.sourceStartSeconds - frozenPosition) < 1e-6
  );
  return {
    passed: status.frozen && allFrozen,
    label: "Frozen read head",
    detail: `read position held at ${frozenPosition.toFixed(3)} s across ${status.grains.length} grains`,
    metrics: { frozenPosition, grains: status.grains.length }
  };
}

export function runAllDiagnostics(sampleRate = 48000) {
  return [
    runImpulseDiagnostic(sampleRate),
    runHannOverlapDiagnostic(sampleRate),
    runConstantToneDiagnostic(sampleRate),
    runVoiceCapDiagnostic(sampleRate),
    runFreezeDiagnostic(sampleRate)
  ];
}
