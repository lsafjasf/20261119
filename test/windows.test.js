import { hannWindow, periodicHannWindow, windowDerivative, timeWeightedWindow, windowEnergy } from '../js/windows.js';
import { describe, it, assert, assertApprox } from './harness.js';

export function runWindowTests() {
  describe('Analysis windows', () => {
    it('symmetric Hann peaks near 1 at the midpoint and stays in [0, 1]', () => {
      const w = hannWindow(128);
      assertApprox(w[63], Math.cos(Math.PI / 254) ** 2, 'even-N midpoint', 1e-12);
      assertApprox(w[0], 0, 'edge', 1e-12);
      for (const v of w) assert(v >= 0 && v <= 1);
    });

    it('periodic Hann starts at zero and peaks at the midpoint', () => {
      const w = periodicHannWindow(64);
      assertApprox(w[0], 0, 'start zero', 1e-12);
      assertApprox(w[32], 1, 'midpoint', 1e-12);
      assert(w[63] > 0, 'last sample is one period step before zero');
    });

    it('finite-difference derivative matches the analytic Hann slope', () => {
      const n = 64;
      const w = hannWindow(n);
      const d = windowDerivative(w);
      for (let i = 1; i < n - 1; i++) {
        const analytic = (Math.PI / (n - 1)) * Math.sin((2 * Math.PI * i) / (n - 1));
        assertApprox(d[i], analytic, `derivative at ${i}`, 1e-3, 1e-4);
      }
    });

    it('centered time weight has zero weighted centroid for a symmetric window', () => {
      const w = hannWindow(128);
      const tw = timeWeightedWindow(w);
      let weightedMean = 0;
      let norm = 0;
      for (let i = 0; i < w.length; i++) {
        weightedMean += tw[i] * w[i];
        norm += w[i];
      }
      assertApprox(weightedMean / norm, 0, 'centered weight mean', 1e-12, 1e-12);
      assert(windowEnergy(w) > 0);
    });
  });
}
