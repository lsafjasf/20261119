// Headless quantitative benchmark for the three mandated test signals.
// Metrics:
//   - frequency concentration: peak-cell frequency error, main-lobe spread,
//     rasterized occupied-cell reduction and peak gain
//   - onset localization: energy-weighted time bias and spread
//   - energy accounting: kept / low-energy / OOB / non-finite buckets

import { reassignedSpectrogram } from '../js/reassigned.js';
import { crossingChirps, closePureTones, transientBurst } from '../js/signals.js';
import {
  toneConcentration,
  onsetLocalization,
  peakCellFrequencyError,
  inBandEnergyFraction,
  rasterConcentration,
  energyAccounting
} from '../js/metrics.js';

function fmt(x, digits = 3) {
  return Number.isFinite(x) ? x.toFixed(digits) : 'NaN';
}

function analyze(signalBuilder, nffts, sr, metricFn) {
  return nffts.map((nfft) => {
    const hop = Math.max(1, Math.floor(nfft / 4));
    const built = signalBuilder();
    const result = reassignedSpectrogram(built.samples, { nfft, hop, sampleRate: built.sampleRate });
    return { nfft, hop, result, built, metrics: metricFn(built, result) };
  });
}

export function runBenchmark(log = console.log) {
  const nffts = [256, 512, 1024, 2048];
  const sr = 16000;
  log('=== Reassignment quantitative benchmark (fs = 16 kHz) ===');

  log('\n[1] Nearby pure tones 2000 / 2250 Hz: frequency concentration');
  const toneRows = analyze(
    () => closePureTones({ sampleRate: sr }),
    nffts, sr,
    (built, result) => ({
      tones: toneConcentration(result, built.meta.tones),
      raster: rasterConcentration(result),
      peakErr: peakCellFrequencyError(result, built.meta.tones.map((t) => () => t.f))
    })
  );
  log('  nfft | hop | grid peak err Hz | re peak err Hz | cells>1% (grid/re) | peak gain x');
  for (const row of toneRows) {
    const occ1 = row.metrics.raster.occupied.find((o) => o.fraction === 0.01);
    log(`  ${row.nfft} | ${row.hop} | ${fmt(row.metrics.peakErr.stftPeakErrorHz, 2).padStart(16)} | ${
      fmt(row.metrics.peakErr.reassignedPeakErrorHz, 4).padStart(14)} | ${
      String(occ1.stftCells).padStart(7)} / ${String(occ1.reassignedCells).padStart(6)} | ${
      fmt(row.metrics.raster.peakGain, 2)}x`);
  }

  log('\n[2] Crossing linear chirps 1-4 kHz: tracking and concentration');
  const chirpRows = analyze(
    () => crossingChirps({ sampleRate: sr, f0: 1000, f1: 4000 }),
    nffts, sr,
    (built, result) => ({
      peakErr: peakCellFrequencyError(result, [
        (t) => built.meta.instantFreqUp(t),
        (t) => built.meta.instantFreqDown(t)
      ]),
      band: inBandEnergyFraction(result, [
        (t) => built.meta.instantFreqUp(t),
        (t) => built.meta.instantFreqDown(t)
      ], sr / result.params.nfft / 2),
      raster: rasterConcentration(result)
    })
  );
  log('  nfft | hop | grid peak err Hz | re peak err Hz | in-band % grid/re | cells>1% grid/re');
  for (const row of chirpRows) {
    const occ1 = row.metrics.raster.occupied.find((o) => o.fraction === 0.01);
    log(`  ${row.nfft} | ${row.hop} | ${fmt(row.metrics.peakErr.stftPeakErrorHz, 2).padStart(16)} | ${
      fmt(row.metrics.peakErr.reassignedPeakErrorHz, 3).padStart(14)} | ${
      fmt(100 * row.metrics.band.stftFraction, 1).padStart(8)} / ${
      fmt(100 * row.metrics.band.reassignedFraction, 1).padStart(6)} | ${
      String(occ1.stftCells).padStart(7)} / ${String(occ1.reassignedCells).padStart(6)}`);
  }

  log('\n[3] Transient burst (3 kHz carrier, sigma 4 ms): onset localization');
  const transientRows = analyze(
    () => transientBurst({ sampleRate: sr, centerSec: 0.25, sigmaSec: 0.004, carrier: 3000 }),
    nffts, sr,
    (built, result) => onsetLocalization(result, built.meta.centerSec)
  );
  log('  nfft | hop | grid bias ms | re bias ms | grid spread ms | re spread ms');
  for (const row of transientRows) {
    const m = row.metrics;
    log(`  ${row.nfft} | ${row.hop} | ${fmt(m.biasStft * 1000, 2).padStart(11)} | ${
      fmt(m.biasReassigned * 1000, 3).padStart(10)} | ${
      fmt(m.spreadStftSec * 1000, 2).padStart(14)} | ${fmt(m.spreadReassignedSec * 1000, 3).padStart(13)}`);
  }

  log('\n[4] Energy accounting and out-of-bounds statistics');
  for (const row of toneRows) {
    const a = energyAccounting(row.result);
    const s = row.result.stats;
    log(`  tones nfft=${row.nfft}: total=${fmt(a.totalEnergy, 1)} gap=${a.absoluteGap.toExponential(1)} ` +
      `OOB(t=${s.oobTimeCells}, f=${s.oobFreqCells}) OOB=${fmt(100 * a.oobFraction, 4)}% ` +
      `lowEnergy=${s.lowEnergyCells} nonFinite=${s.nonFiniteCells}`);
  }

  return { toneRows, chirpRows, transientRows };
}

const invokedPath = process.argv[1] ? process.argv[1].replace(/\\/g, '/') : '';
if (invokedPath.endsWith('benchmark.js')) runBenchmark();
