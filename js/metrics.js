// Quantitative comparison of the ordinary STFT grid against reassignment.
// All metrics use energy-weighted statistics over active (above-floor) cells.

import { CellStatus } from './reassigned.js';

// Energy-weighted RMS error (Hz) between cell frequencies and a reference.
// "reassigned" uses continuous reFreq; "stft" uses the quantized origFreq.
export function frequencyRMSE(result, freqReferenceHz, { reassigned = true } = {}) {
  let acc = 0;
  let weight = 0;
  const freq = reassigned ? result.reFreq : result.origFreq;
  for (let idx = 0; idx < result.energy.length; idx++) {
    if (result.status[idx] !== CellStatus.KEPT) continue;
    const e = result.energy[idx];
    const t = result.origTime[idx];
    const ref = typeof freqReferenceHz === 'function' ? freqReferenceHz(t, idx) : freqReferenceHz;
    const d = freq[idx] - ref;
    acc += e * d * d;
    weight += e;
  }
  if (weight === 0) return { rmseHz: NaN, weight: 0 };
  return { rmseHz: Math.sqrt(acc / weight), weight };
}

// Per-frame peak-cell frequency error against reference laws. The peak cell
// carries the dominant local energy, so its error measures how precisely the
// strongest component is localized. Reference is chosen per cell by nearest
// reassigned frequency.
export function peakCellFrequencyError(result, laws) {
  const { frames, halfBins } = result.params;
  let stftErr = 0;
  let reErr = 0;
  let weight = 0;
  for (let m = 0; m < frames; m++) {
    let peakIdx = -1;
    let peakEnergy = 0;
    for (let k = 0; k < halfBins; k++) {
      const idx = m * halfBins + k;
      if (result.status[idx] === CellStatus.KEPT && result.energy[idx] > peakEnergy) {
        peakEnergy = result.energy[idx];
        peakIdx = idx;
      }
    }
    if (peakIdx < 0) continue;
    const t = result.origTime[peakIdx];
    let nearest = laws[0](t);
    let nearestDist = Infinity;
    for (const law of laws) {
      const f = law(t);
      const d = Math.abs(f - result.reFreq[peakIdx]);
      if (d < nearestDist) {
        nearestDist = d;
        nearest = f;
      }
    }
    stftErr += peakEnergy * Math.abs(result.origFreq[peakIdx] - nearest);
    reErr += peakEnergy * Math.abs(result.reFreq[peakIdx] - nearest);
    weight += peakEnergy;
  }
  if (weight === 0) return { stftPeakErrorHz: NaN, reassignedPeakErrorHz: NaN, weight: 0 };
  return {
    stftPeakErrorHz: stftErr / weight,
    reassignedPeakErrorHz: reErr / weight,
    weight
  };
}

// Frequency spread (Hz) around each reference tone, restricted to the tone's
// main-lobe cells (the strongest local cluster), where reassignment sharpness
// is meaningful; far sidelobes are reported separately by inBandEnergyFraction.
export function toneConcentration(result, referenceTones) {
  // Main-lobe cells (above half the frame peak) attributed to the nearest
  // reference tone. Measures concentration where component energy dominates,
  // rather than letting distant low-energy sidelobes dominate an RMS average.
  const accum = referenceTones.map(({ f: refHz, amp }) => ({
    refHz, amp, spreadStft: 0, spreadRe: 0, weight: 0
  }));
  const { frames, halfBins } = result.params;
  const framePeak = new Float64Array(frames);
  for (let m = 0; m < frames; m++) {
    let peak = 0;
    for (let k = 0; k < halfBins; k++) {
      const idx = m * halfBins + k;
      if (result.status[idx] === CellStatus.KEPT) peak = Math.max(peak, result.energy[idx]);
    }
    framePeak[m] = peak;
  }
  for (let m = 0; m < frames; m++) {
    if (framePeak[m] === 0) continue;
    for (let k = 0; k < halfBins; k++) {
      const idx = m * halfBins + k;
      if (result.status[idx] !== CellStatus.KEPT || result.energy[idx] < 0.5 * framePeak[m]) continue;
    let nearest = 0;
    let nearestDist = Infinity;
    for (let t = 0; t < referenceTones.length; t++) {
      const d = Math.abs(result.reFreq[idx] - referenceTones[t].f);
      if (d < nearestDist) {
        nearestDist = d;
        nearest = t;
      }
    }
    const e = result.energy[idx];
    const refHz = referenceTones[nearest].f;
    accum[nearest].spreadStft += e * (result.origFreq[idx] - refHz) ** 2;
    accum[nearest].spreadRe += e * (result.reFreq[idx] - refHz) ** 2;
    accum[nearest].weight += e;
  }
  }
  return accum.map((a) => {
    const base = a.weight || 1;
    return {
      refHz: a.refHz,
      amp: a.amp,
      weight: a.weight,
      stftSpreadHz: Math.sqrt(a.spreadStft / base),
      reassignedSpreadHz: Math.sqrt(a.spreadRe / base)
    };
  });
}

// Peakiness alternative for chirps and mid-bin signals where grid quantization
// RMSE is naturally tiny: fraction of active energy landing within a tolerance
// band of a reference law, for both grids.
export function inBandEnergyFraction(result, laws, bandHz) {
  let stftIn = 0;
  let reIn = 0;
  let weight = 0;
  for (let idx = 0; idx < result.energy.length; idx++) {
    if (result.status[idx] !== CellStatus.KEPT) continue;
    const t = result.origTime[idx];
    let distStft = Infinity;
    let distRe = Infinity;
    for (const law of laws) {
      const f = law(t);
      distStft = Math.min(distStft, Math.abs(result.origFreq[idx] - f));
      distRe = Math.min(distRe, Math.abs(result.reFreq[idx] - f));
    }
    const e = result.energy[idx];
    if (distStft <= bandHz) stftIn += e;
    if (distRe <= bandHz) reIn += e;
    weight += e;
  }
  if (weight === 0) return { bandHz, stftFraction: NaN, reassignedFraction: NaN, weight: 0 };
  return { bandHz, stftFraction: stftIn / weight, reassignedFraction: reIn / weight, weight };
}

// Onset/localization error for a transient: energy-weighted mean time and its
// spread, compared against a reference time (burst center or half-max onset).
export function onsetLocalization(result, referenceTimeSec) {
  let meanStft = 0;
  let meanRe = 0;
  let weight = 0;
  for (let idx = 0; idx < result.energy.length; idx++) {
    if (result.status[idx] !== CellStatus.KEPT) continue;
    const e = result.energy[idx];
    meanStft += e * result.origTime[idx];
    meanRe += e * result.reTime[idx];
    weight += e;
  }
  if (weight === 0) {
    return { weight: 0, meanStft: NaN, meanReassigned: NaN, biasStft: NaN, biasReassigned: NaN };
  }
  meanStft /= weight;
  meanRe /= weight;

  let varStft = 0;
  let varRe = 0;
  for (let idx = 0; idx < result.energy.length; idx++) {
    if (result.status[idx] !== CellStatus.KEPT) continue;
    const e = result.energy[idx];
    varStft += e * (result.origTime[idx] - meanStft) ** 2;
    varRe += e * (result.reTime[idx] - meanRe) ** 2;
  }
  return {
    weight,
    referenceTimeSec,
    meanStft,
    meanReassigned: meanRe,
    biasStft: meanStft - referenceTimeSec,
    biasReassigned: meanRe - referenceTimeSec,
    spreadStftSec: Math.sqrt(varStft / weight),
    spreadReassignedSec: Math.sqrt(varRe / weight)
  };
}

// Chirp tracking: match every active cell to the nearest of two instantaneous
// frequency laws at its frame time, then report weighted RMSE for both grids.
export function chirpTrackingRMSE(result, laws) {
  let accStft = 0;
  let accRe = 0;
  let weight = 0;
  for (let idx = 0; idx < result.energy.length; idx++) {
    if (result.status[idx] !== CellStatus.KEPT) continue;
    const t = result.origTime[idx];
    let nearest = laws[0](t);
    let nearestDist = Infinity;
    for (const law of laws) {
      const f = law(t);
      const d = Math.abs(f - result.reFreq[idx]);
      if (d < nearestDist) {
        nearestDist = d;
        nearest = f;
      }
    }
    const e = result.energy[idx];
    accStft += e * (result.origFreq[idx] - nearest) ** 2;
    accRe += e * (result.reFreq[idx] - nearest) ** 2;
    weight += e;
  }
  if (weight === 0) return { rmseStftHz: NaN, rmseReassignedHz: NaN, weight: 0 };
  return {
    rmseStftHz: Math.sqrt(accStft / weight),
    rmseReassignedHz: Math.sqrt(accRe / weight),
    weight
  };
}

// Rasterized concentration: how many grid cells exceed a fraction of the map
// peak, and how strongly the single strongest cell concentrates energy.
// Reassignment should reduce the occupied count and raise the peak fraction.
export function rasterConcentration(result, peakFractions = [0.01, 0.1]) {
  let stftPeak = 0;
  let rePeak = 0;
  for (const v of result.spectrogram) stftPeak = Math.max(stftPeak, v);
  for (const v of result.reassignedGrid) rePeak = Math.max(rePeak, v);
  const stftTotal = result.stats.totalEnergy || 1;
  const occupied = peakFractions.map((frac) => {
    let stftCount = 0;
    let reCount = 0;
    for (const v of result.spectrogram) if (v >= frac * stftPeak) stftCount++;
    for (const v of result.reassignedGrid) if (v >= frac * rePeak) reCount++;
    return {
      fraction: frac,
      stftCells: stftCount,
      reassignedCells: reCount,
      reductionRatio: stftCount > 0 ? reCount / stftCount : NaN
    };
  });
  return {
    occupied,
    stftPeakFraction: stftPeak / stftTotal,
    reassignedPeakFraction: rePeak / stftTotal,
    peakGain: stftPeak > 0 ? rePeak / stftPeak : NaN
  };
}

// Energy accounting sanity: every cell must land in exactly one bucket.
export function energyAccounting(result) {
  const s = result.stats;
  const bucketed =
    s.keptEnergy + s.oobEnergy + s.lowEnergyEnergy + s.nonFiniteEnergy;
  return {
    totalEnergy: s.totalEnergy,
    bucketedEnergy: bucketed,
    absoluteGap: Math.abs(bucketed - s.totalEnergy),
    oobFraction: s.totalEnergy > 0 ? s.oobEnergy / s.totalEnergy : 0
  };
}
