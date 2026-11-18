import { reassignedSpectrogram } from '../js/reassigned.js';
import { decibelGrid } from '../js/render.js';
import { closePureTones } from '../js/signals.js';
import { describe, it, assert } from './harness.js';

export function runRenderTests() {
  describe('Render pipeline', () => {
    it('normalizes both grids into the [0, 1] display range', () => {
      const sig = closePureTones({ sampleRate: 16000 });
      const out = reassignedSpectrogram(sig.samples, {
        nfft: 512, hop: 128, sampleRate: sig.sampleRate
      });
      for (const key of ['spectrogram', 'reassignedGrid']) {
        const img = decibelGrid(out, key, 80);
        assert(img.length === out[key].length, `${key} length preserved`);
        let min = 1;
        let max = 0;
        for (const v of img) {
          assert(v >= 0 && v <= 1, `${key} value in range`);
          min = Math.min(min, v);
          max = Math.max(max, v);
        }
        assert(max === 1, `${key} peak maps to 1.0`);
        assert(min >= 0 && min <= 1, `${key} stays within the normalized range`);
      }
    });

    it('handles an all-zero grid without NaN or division artifacts', () => {
      const silence = new Float64Array(4096);
      const out = reassignedSpectrogram(silence, { nfft: 256, hop: 64, sampleRate: 8000 });
      const img = decibelGrid(out, 'reassignedGrid', 80);
      for (const v of img) {
        assert(Number.isFinite(v), 'display values must stay finite on silence');
        assert(v === 0, 'silent map renders at the floor color');
      }
    });
  });
}
