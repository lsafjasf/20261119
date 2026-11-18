// Reassigned STFT (Auger-Flandrin operators, centered discrete convention).
//
// For each analysis cell (frame m, bin k) we evaluate three FFTs over the
// windowed segment:
//   X  = FFT{x[n] w[n]}
//   Xd = FFT{x[n] w'[n] e^{+i*w_k*n}}   (phase-rotated window derivative)
//   Xt = FFT{x[n] (n-c) w[n]}           (centered time weight, c=(N-1)/2)
// The rotation in Xd is essential: applying the group-delay/IF identities to
// the non-centered FFT introduces an extra e^{+i*w_k*n} factor. With it:
//   omega_hat = omega_k - Im(conj(X) * Xd) / |X|^2      (rad/sample)
//   t_hat     = t_m     + Re(conj(X) * Xt) / |X|^2      (samples)
// For a stationary tone Xd = -i*omega0*X, so -Im(conj(X)Xd)/|X|^2 = +omega0
// and omega_hat recovers the true angular frequency exactly.
// Division only happens for cells whose energy clears the floor, so zero
// energy can never produce a divide-by-zero.

import { fft, fftSizeSupported } from './fft.js';
import { periodicHannWindow, windowDerivative, timeWeightedWindow } from './windows.js';

export const CellStatus = Object.freeze({
  KEPT: 0,
  LOW_ENERGY: 1,
  NON_FINITE: 2,
  OUT_OF_BOUNDS_TIME: 3,
  OUT_OF_BOUNDS_FREQ: 4
});

export const STATUS_LABEL = Object.freeze({
  0: 'kept',
  1: 'low-energy (below floor, no reassignment)',
  2: 'non-finite reassignment (NaN/Inf, rejected)',
  3: 'out-of-bounds time (reassigned before first / after last frame)',
  4: 'out-of-bounds frequency (reassigned below 0 Hz or above Nyquist)'
});

export function stftFrameCount(inputLength, nfft, hop) {
  if (!fftSizeSupported(nfft)) throw new Error(`nfft ${nfft} must be a power of two`);
  if (hop <= 0) throw new Error('hop must be positive');
  return Math.max(0, Math.floor((inputLength - nfft) / hop) + 1);
}

// Main entry. Options:
//   nfft, hop, sampleRate, window (optional), relFloor (default 1e-12),
//   absFloor (default 0). Returns grid-aligned and per-cell data.
export function reassignedSpectrogram(samples, options = {}) {
  const nfft = options.nfft ?? 1024;
  const hop = options.hop ?? Math.max(1, Math.floor(nfft / 4));
  const sampleRate = options.sampleRate ?? 1;
  const relFloor = options.relFloor ?? 1e-12;
  const absFloor = options.absFloor ?? 0;
  if (!fftSizeSupported(nfft)) throw new Error(`nfft ${nfft} must be a power of two`);

  const window = options.window ?? periodicHannWindow(nfft);
  if (window.length !== nfft) throw new Error('window length must equal nfft');
  const derivative = options.derivative ?? windowDerivative(window);
  const timeWeighted = options.timeWeighted ?? timeWeightedWindow(window);

  const inputLength = samples.length;
  const frames = stftFrameCount(inputLength, nfft, hop);
  const halfBins = nfft / 2 + 1;
  const cells = frames * halfBins;
  const half = (nfft - 1) / 2;

  const re = new Float64Array(nfft);
  const im = new Float64Array(nfft);
  const reD = new Float64Array(nfft);
  const imD = new Float64Array(nfft);
  const reT = new Float64Array(nfft);
  const imT = new Float64Array(nfft);

  const energy = new Float64Array(cells);
  const origTime = new Float64Array(cells);
  const origFreq = new Float64Array(cells);
  const reTime = new Float64Array(cells);
  const reFreq = new Float64Array(cells);
  const status = new Uint8Array(cells);
  const targetFrame = new Int32Array(cells).fill(-1);
  const targetBin = new Int32Array(cells).fill(-1);
  const spectrogram = new Float64Array(cells);
  const reassignedGrid = new Float64Array(cells);

  const stats = {
    frames,
    halfBins,
    nfft,
    hop,
    sampleRate,
    totalEnergy: 0,
    keptCells: 0,
    keptEnergy: 0,
    lowEnergyCells: 0,
    lowEnergyEnergy: 0,
    nonFiniteCells: 0,
    nonFiniteEnergy: 0,
    oobTimeCells: 0,
    oobFreqCells: 0,
    oobEnergy: 0,
    energyFloor: 0,
    maxCellEnergy: 0
  };

  // First pass: energies, so the floor can be defined relative to the peak.
  const allEnergy = new Float64Array(cells);
  let maxCellEnergy = 0;
  for (let m = 0; m < frames; m++) {
    const start = m * hop;
    for (let n = 0; n < nfft; n++) re[n] = samples[start + n] * window[n];
    im.fill(0);
    fft(re, im);
    const base = m * halfBins;
    for (let k = 0; k < halfBins; k++) {
      const e = re[k] * re[k] + im[k] * im[k];
      allEnergy[base + k] = e;
      if (e > maxCellEnergy) maxCellEnergy = e;
    }
  }
  const energyFloor = Math.max(absFloor, maxCellEnergy * relFloor);
  stats.energyFloor = energyFloor;
  stats.maxCellEnergy = maxCellEnergy;

  analyzeFrames();
  rasterize();

  return {
    params: { nfft, hop, sampleRate, frames, halfBins, windowName: options.windowName ?? 'periodic-hann' },
    energy,
    origTime,
    origFreq,
    reTime,
    reFreq,
    status,
    targetFrame,
    targetBin,
    spectrogram,
    reassignedGrid,
    stats
  };

  function analyzeFrames() {
    for (let m = 0; m < frames; m++) {
      const start = m * hop;
      const centerSample = start + half;
      for (let n = 0; n < nfft; n++) {
        const x = samples[start + n];
        re[n] = x * window[n];
        reD[n] = x * derivative[n];
        reT[n] = x * timeWeighted[n];
      }
      im.fill(0); imD.fill(0); imT.fill(0);
      fft(re, im);
      fft(reD, imD);
      fft(reT, imT);

      const base = m * halfBins;
      for (let k = 0; k < halfBins; k++) {
        const idx = base + k;
        const e = allEnergy[idx];
        energy[idx] = e;
        stats.totalEnergy += e;
        const t0 = centerSample / sampleRate;
        const f0 = (k * sampleRate) / nfft;
        origTime[idx] = t0;
        origFreq[idx] = f0;
        spectrogram[idx] = e;

        // Explicit guard: no division on zero / sub-floor energy.
        if (!(e > 0) || e < energyFloor) {
          status[idx] = CellStatus.LOW_ENERGY;
          stats.lowEnergyCells++;
          stats.lowEnergyEnergy += e;
          reTime[idx] = NaN;
          reFreq[idx] = NaN;
          continue;
        }

        const xRe = re[k];
        const xIm = im[k];
        // Rotated derivative coefficient Xd_rot[k] = FFT{x w' e^{+i w_k n}}[k].
        // Multiplication by e^{+i w_k n} shifts the spectrum by +k bins, so
        // the value at output bin k equals the unrotated FFT at index 0 after
        // the shift; equivalently read FFT{x w'} at index ((j - k) mod N).
        const omegaK = (2 * Math.PI * k) / nfft;
        const srcIndex = (0 - k + nfft) % nfft;
        const xdRe = reD[srcIndex];
        const xdIm = imD[srcIndex];
        // Im(conj(X) * Xd) and Re(conj(X) * Xt)
        const imagXd = xdIm * xRe - xdRe * xIm;
        const realXt = xRe * reT[k] + xIm * imT[k];
        const omegaHat = omegaK - imagXd / e;
        const tHatSamples = centerSample + realXt / e;
        const tHatSec = tHatSamples / sampleRate;
        const fHat = (omegaHat * sampleRate) / (2 * Math.PI);
        reTime[idx] = tHatSec;
        reFreq[idx] = fHat;

        if (!Number.isFinite(tHatSec) || !Number.isFinite(fHat)) {
          status[idx] = CellStatus.NON_FINITE;
          stats.nonFiniteCells++;
          stats.nonFiniteEnergy += e;
          continue;
        }

        const tf = Math.round((tHatSamples - half) / hop);
        const kb = Math.round((fHat * nfft) / sampleRate);
        const oobTime = tf < 0 || tf >= frames;
        const oobFreq = kb < 0 || kb >= halfBins;
        if (oobTime || oobFreq) {
          status[idx] = oobTime ? CellStatus.OUT_OF_BOUNDS_TIME : CellStatus.OUT_OF_BOUNDS_FREQ;
          stats.oobEnergy += e;
          if (oobTime) stats.oobTimeCells++;
          if (oobFreq) stats.oobFreqCells++;
          continue;
        }

        targetFrame[idx] = tf;
        targetBin[idx] = kb;
        status[idx] = CellStatus.KEPT;
        stats.keptCells++;
        stats.keptEnergy += e;
      }
    }
  }

  // Accumulate kept-cell energy onto the target grid; OOB energy stays out.
  function rasterize() {
    for (let idx = 0; idx < cells; idx++) {
      if (status[idx] !== CellStatus.KEPT) continue;
      const tf = targetFrame[idx];
      const kb = targetBin[idx];
      if (tf < 0 || tf >= frames || kb < 0 || kb >= halfBins) {
        // Defensive: classification and rasterization must agree.
        status[idx] = CellStatus.NON_FINITE;
        continue;
      }
      reassignedGrid[tf * halfBins + kb] += energy[idx];
    }
  }
}
