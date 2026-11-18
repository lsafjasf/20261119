import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const require = createRequire(import.meta.url);
const Core = require("../js/synth-core.js");
const sampleRate = 48000;
const outDir = resolve("artifacts");
mkdirSync(outDir, { recursive: true });

const events = [
  { id: "demo-1", type: "pluck", time: 0, midi: 57, frequency: 220, position: 0.21, damping: 0.38, hardness: 0.62, velocity: 0.86, seed: 0x2a11 },
  { id: "demo-2", type: "pluck", time: 0.72, midi: 64, frequency: Core.midiToFrequency(64), position: 0.28, damping: 0.34, hardness: 0.7, velocity: 0.72, seed: 0x2a12 },
  { id: "demo-3", type: "pluck", time: 1.42, midi: 69, frequency: 440, position: 0.33, damping: 0.54, hardness: 0.55, velocity: 0.8, seed: 0x2a13 },
  { id: "demo-4", type: "pluck", time: 2.08, midi: 57, frequency: 220, position: 0.2, damping: 0.7, hardness: 0.78, velocity: 0.9, seed: 0x2a14 }
];

const timeline = Core.renderTimeline(events, { sampleRate });
writeFileSync(resolve(outDir, "waveguide-demo.wav"), Core.encodeWav(timeline.samples, sampleRate));
writeFileSync(resolve(outDir, "waveguide-events.json"), JSON.stringify({
  format: "waveguide-string-events",
  version: 1,
  engine: "WG-04 digital waveguide",
  sampleRate,
  a4: 440,
  synthesis: {
    delayLine: true,
    fractionalAllpass: true,
    frequencyDependentLoss: true,
    pluckPosition: true,
    proceduralExcitation: true
  },
  events
}, null, 2));

const pitchSweep = [40, 57, 64, 69].map((midi) => {
  const target = Core.midiToFrequency(midi);
  const render = Core.renderNote({
    sampleRate,
    frequency: target,
    position: 0.2,
    damping: 0.4,
    hardness: 0.6,
    duration: 1.5,
    seed: 0x4a10 + midi
  });
  const tracked = Core.spectralPitchTrack(render.samples, sampleRate, target);
  return {
    midi,
    note: Core.midiToNoteName(midi),
    targetHz: Number(target.toFixed(4)),
    measuredHz: Number(tracked.measuredFrequency.toFixed(4)),
    errorCents: Number(tracked.cents.toFixed(4)),
    harmonicPeaks: tracked.peaks.map((peak) => ({
      harmonic: peak.harmonic,
      frequency: Number(peak.frequency.toFixed(3))
    }))
  };
});

const notchComparison = Core.comparePluckPoints({
  sampleRate,
  frequency: 220,
  damping: 0.4,
  hardness: 0.6,
  positionA: 0.2,
  positionB: 0.33,
  harmonicCount: 12
});

const decayTests = [0.2, 0.75].map((damping) => {
  const render = Core.renderNote({
    sampleRate,
    frequency: 220,
    position: 0.2,
    damping,
    hardness: 0.6,
    duration: 6,
    seed: 0x77aa
  });
  return {
    damping,
    targetT60: Number(render.stats.targetT60.toFixed(3)),
    measuredT60: Number(render.stats.t60.toFixed(3)),
    residualPeak: render.stats.residualPeak
  };
});

const report = {
  generatedAt: new Date().toISOString(),
  sampleRate,
  pitchSweep,
  pluckNotchComparison: {
    pitchHz: 220,
    positionA: 0.2,
    positionB: 0.33,
    harmonics: notchComparison.harmonics.map((harmonic) => ({
      harmonic: harmonic.harmonic,
      aDb: Number(harmonic.dbA.toFixed(2)),
      bDb: Number(harmonic.dbB.toFixed(2))
    }))
  },
  decayTests,
  idleSafety: {
    loopGainBelowUnity: Core.loopParameters(220, sampleRate, 0, 1).loopGain < 1,
    finiteRenderedDuration: true,
    maximumTailResidual: Math.max(...decayTests.map((test) => test.residualPeak))
  },
  sources: {
    prerecordedSamples: 0,
    excitation: "deterministic triangular pluck displacement plus seeded noise"
  }
};
writeFileSync(resolve(outDir, "analysis-report.json"), JSON.stringify(report, null, 2));

console.log(`Wrote ${timeline.duration.toFixed(2)}s WAV, ${events.length} events, and analysis report.`);
