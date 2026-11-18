import { reassignedSpectrogram, CellStatus } from '../js/reassigned.js';
import { hannWindow } from '../js/windows.js';
import { closePureTones, transientBurst } from '../js/signals.js';
import { describe, it, assert, assertApprox } from './harness.js';

export function runReassignmentTests() {
  describe('Reassignment core', () => {
    it('snaps pure tones to their exact carrier frequencies', () => {
      const sig = closePureTones({ sampleRate: 16000, fA: 2000, fB: 2250 });
      const out = reassignedSpectrogram(sig.samples, {
        nfft: 1024, hop: 256, sampleRate: sig.sampleRate
      });
      const mid = Math.floor(out.params.frames / 2);
      let found2000 = false;
      let found2250 = false;
      for (let k = 0; k < out.params.halfBins; k++) {
        const idx = mid * out.params.halfBins + k;
        if (out.status[idx] !== CellStatus.KEPT) continue;
        if (Math.abs(out.reFreq[idx] - 2000) < 1) found2000 = true;
        if (Math.abs(out.reFreq[idx] - 2250) < 1) found2250 = true;
      }
      assert(found2000, 'no cell reassigned near 2000 Hz');
      assert(found2250, 'no cell reassigned near 2250 Hz');
    });

    it('localizes a transient at its reference center within one hop', () => {
      const sig = transientBurst({ sampleRate: 16000, centerSec: 0.25, sigmaSec: 0.004 });
      const hop = 128;
      const out = reassignedSpectrogram(sig.samples, {
        nfft: 512, hop, sampleRate: sig.sampleRate
      });
      let mean = 0;
      let weight = 0;
      for (let idx = 0; idx < out.energy.length; idx++) {
        if (out.status[idx] !== CellStatus.KEPT) continue;
        mean += out.reTime[idx] * out.energy[idx];
        weight += out.energy[idx];
      }
      mean /= weight;
      assertApprox(mean, sig.meta.centerSec, 'transient center', 0.01);
      assert(Math.abs(mean - sig.meta.centerSec) < hop / sig.sampleRate,
        'reassignment must beat the STFT hop grid');
    });

    it('never divides by zero on silence and stores no infinities', () => {
      const silence = new Float64Array(4096);
      const out = reassignedSpectrogram(silence, { nfft: 256, hop: 64, sampleRate: 8000 });
      assert(out.stats.keptCells === 0, 'silence must produce no kept cells');
      assert(out.stats.totalEnergy === 0, 'silence energy is exactly zero');
      for (let idx = 0; idx < out.energy.length; idx++) {
        if (out.status[idx] === CellStatus.KEPT) throw new Error('silent cell marked kept');
        assert(!Number.isFinite(out.reTime[idx]) || !Number.isNaN(out.origTime[idx]));
      }
    });

    it('classifies leading-edge reassignment as time out-of-bounds', () => {
      const nfft = 256;
      const hop = 32;
      const sr = 8000;
      const impulse = new Float64Array(8192);
      impulse[2] = 1;
      const out = reassignedSpectrogram(impulse, {
        nfft, hop, sampleRate: sr, relFloor: 0, window: hannWindow(nfft), windowName: 'hann'
      });
      assert(out.stats.oobTimeCells > 0, 'expected time-OOB cells at the leading edge');
      assert(out.stats.oobEnergy > 0, 'OOB energy must be accumulated separately');
      const idx = out.status.indexOf(CellStatus.OUT_OF_BOUNDS_TIME);
      assert(out.targetFrame[idx] < 0, 'OOB target frame must be negative');
      assert(out.reTime[idx] >= 0, 'reassigned time itself is still a finite physical time');
    });

    it('counts out-of-Nyquist reassignment as frequency out-of-bounds', () => {
      const nfft = 256;
      const hop = 64;
      const sr = 8000;
      const tone = new Float64Array(8192);
      for (let n = 0; n < tone.length; n++) tone[n] = Math.sin(Math.PI * n);
      const out = reassignedSpectrogram(tone, { nfft, hop, sampleRate: sr, relFloor: 0 });
      assert(out.stats.oobFreqCells > 0, 'Nyquist-edge numerical cells must be flagged');
      for (let idx = 0; idx < out.status.length; idx++) {
        if (out.status[idx] === CellStatus.OUT_OF_BOUNDS_FREQ) {
          assert(out.reFreq[idx] < 0 || out.reFreq[idx] > sr / 2, 'freq OOB coordinate outside range');
        }
      }
    });

    it('conserves energy across kept, OOB, low-energy and non-finite buckets', () => {
      const sig = closePureTones({});
      const out = reassignedSpectrogram(sig.samples, {
        nfft: 1024, hop: 256, sampleRate: sig.sampleRate
      });
      const s = out.stats;
      const bucketed = s.keptEnergy + s.oobEnergy + s.lowEnergyEnergy + s.nonFiniteEnergy;
      assertApprox(bucketed, s.totalEnergy, 'energy accounting', 1e-12, 1e-9);
    });

    it('rasterized reassigned grid only receives kept-cell energy', () => {
      const sig = closePureTones({});
      const out = reassignedSpectrogram(sig.samples, {
        nfft: 1024, hop: 256, sampleRate: sig.sampleRate
      });
      let gridSum = 0;
      for (let idx = 0; idx < out.reassignedGrid.length; idx++) gridSum += out.reassignedGrid[idx];
      assertApprox(gridSum, out.stats.keptEnergy, 'grid holds kept energy', 1e-12, 1e-9);
    });
  });
}
