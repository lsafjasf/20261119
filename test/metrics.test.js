import { reassignedSpectrogram } from '../js/reassigned.js';
import { crossingChirps, closePureTones, transientBurst } from '../js/signals.js';
import {
  toneConcentration,
  onsetLocalization,
  peakCellFrequencyError,
  inBandEnergyFraction,
  rasterConcentration,
  energyAccounting,
  frequencyRMSE
} from '../js/metrics.js';
import { describe, it, assert } from './harness.js';

export function runMetricsTests() {
  describe('Quantitative metrics', () => {
    it('reassignment snaps the peak cell of each tone to its exact carrier', () => {
      const sig = closePureTones({ sampleRate: 16000 });
      const out = reassignedSpectrogram(sig.samples, {
        nfft: 1024, hop: 256, sampleRate: sig.sampleRate
      });
      const peak = peakCellFrequencyError(out, sig.meta.tones.map((t) => () => t.f));
      const binHz = sig.sampleRate / 1024;
      assert(peak.reassignedPeakErrorHz < 0.05,
        `reassigned peak error ${peak.reassignedPeakErrorHz} should be sub-0.05 Hz`);
      assert(peak.reassignedPeakErrorHz < binHz * 0.01, 'sub-percent-bin carrier localization');
      const rows = toneConcentration(out, sig.meta.tones);
      for (const row of rows) assert(row.reassignedSpreadHz < 1, 'main-lobe reassigned spread near zero');
    });

    it('reduces rasterized occupied-cell count and concentrates the peak', () => {
      const sig = closePureTones({ sampleRate: 16000 });
      const out = reassignedSpectrogram(sig.samples, {
        nfft: 1024, hop: 256, sampleRate: sig.sampleRate
      });
      const r = rasterConcentration(out);
      const occ1 = r.occupied.find((o) => o.fraction === 0.01);
      assert(occ1.reassignedCells < occ1.stftCells, 'fewer cells above 1% peak after reassignment');
      assert(r.peakGain > 1, `peak gain ${r.peakGain} must exceed 1`);
    });

    it('tracks crossing chirps at least as accurately and focuses more energy', () => {
      const sig = crossingChirps({ sampleRate: 16000, f0: 1000, f1: 4000 });
      const nfft = 1024;
      const out = reassignedSpectrogram(sig.samples, {
        nfft, hop: 256, sampleRate: sig.sampleRate
      });
      const laws = [
        (t) => sig.meta.instantFreqUp(t),
        (t) => sig.meta.instantFreqDown(t)
      ];
      const m = peakCellFrequencyError(out, laws);
      const binHz = sig.sampleRate / nfft;
      assert(m.reassignedPeakErrorHz <= m.stftPeakErrorHz, 'reassigned peak error no worse than grid');
      assert(m.reassignedPeakErrorHz < binHz, `peak error ${m.reassignedPeakErrorHz} under one bin`);
      const band = inBandEnergyFraction(out, laws, binHz / 2);
      assert(band.reassignedFraction >= band.stftFraction, 'more energy inside the half-bin band');
    });

    it('transient onset bias and time spread both shrink after reassignment', () => {
      const sig = transientBurst({ sampleRate: 16000, centerSec: 0.25, sigmaSec: 0.004 });
      const out = reassignedSpectrogram(sig.samples, {
        nfft: 512, hop: 128, sampleRate: sig.sampleRate
      });
      const m = onsetLocalization(out, sig.meta.centerSec);
      assert(Math.abs(m.biasReassigned) < Math.abs(m.biasStft),
        `bias ${m.biasReassigned} vs ${m.biasStft}`);
      assert(m.spreadReassignedSec < m.spreadStftSec,
        `spread ${m.spreadReassignedSec} vs ${m.spreadStftSec}`);
    });

    it('frequencyRMSE helper agrees on a constant reference', () => {
      const sig = closePureTones({ sampleRate: 16000, fA: 3000, fB: 3100 });
      const out = reassignedSpectrogram(sig.samples, {
        nfft: 2048, hop: 512, sampleRate: sig.sampleRate
      });
      const re = frequencyRMSE(out, 3000, { reassigned: true });
      const stft = frequencyRMSE(out, 3000, { reassigned: false });
      assert(Number.isFinite(re.rmseHz) && Number.isFinite(stft.rmseHz));
      assert(re.weight > 0 && stft.weight > 0);
    });

    it('energyAccounting reports a zero gap for well-conditioned signals', () => {
      const sig = closePureTones({});
      const out = reassignedSpectrogram(sig.samples, {
        nfft: 1024, hop: 256, sampleRate: sig.sampleRate
      });
      const a = energyAccounting(out);
      assert(a.absoluteGap <= 1e-9, `gap was ${a.absoluteGap}`);
      assert(a.oobFraction >= 0 && a.oobFraction <= 1);
    });
  });
}
