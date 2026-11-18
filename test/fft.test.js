import { fft, fftCopy, fftSizeSupported } from '../js/fft.js';
import { describe, it, assert, assertApprox } from './harness.js';

export function runFftTests() {
  describe('FFT correctness', () => {
    it('rejects non-power-of-two lengths', () => {
      assert(fftSizeSupported(1024));
      assert(!fftSizeSupported(1000));
      let threw = false;
      try {
        fft(new Float64Array(3), new Float64Array(3));
      } catch {
        threw = true;
      }
      assert(threw, 'length 3 should throw');
    });

    it('matches a direct DFT on a random length-16 signal', () => {
      const n = 16;
      const re = Float64Array.from({ length: n }, (_, i) => Math.sin(i * 1.7) + 0.5 * Math.cos(i * 0.3));
      const im = new Float64Array(n);
      fft(re, im);
      const input = Float64Array.from({ length: n }, (_, i) => Math.sin(i * 1.7) + 0.5 * Math.cos(i * 0.3));
      for (let k = 0; k < n; k++) {
        let sumRe = 0;
        let sumIm = 0;
        for (let j = 0; j < n; j++) {
          const angle = (-2 * Math.PI * k * j) / n;
          sumRe += input[j] * Math.cos(angle);
          sumIm += input[j] * Math.sin(angle);
        }
        assertApprox(re[k], sumRe, `real bin ${k}`, 1e-10, 1e-10);
        assertApprox(im[k], sumIm, `imag bin ${k}`, 1e-10, 1e-10);
      }
    });

    it('places a complex exponential at the expected bin', () => {
      const n = 64;
      const k0 = 9;
      const re = new Float64Array(n);
      const im = new Float64Array(n);
      for (let i = 0; i < n; i++) {
        re[i] = Math.cos((2 * Math.PI * k0 * i) / n);
        im[i] = Math.sin((2 * Math.PI * k0 * i) / n);
      }
      fft(re, im);
      assertApprox(re[k0], n, 'bin energy real', 1e-10);
      assertApprox(im[k0], 0, 'bin energy imag', 1e-10, 1e-8);
    });

    it('fftCopy does not mutate the caller buffers', () => {
      const input = Float64Array.from([1, 2, 3, 4]);
      const snapshot = Float64Array.from(input);
      fftCopy(input);
      for (let i = 0; i < input.length; i++) assert(input[i] === snapshot[i]);
    });
  });
}
