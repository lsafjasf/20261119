// Radix-2 iterative Cooley-Tukey FFT (in place, power-of-two length only).
// Returns an object with Float64Array re/im buffers shared across calls.

export function fftSizeSupported(n) {
  return Number.isInteger(n) && n >= 2 && (n & (n - 1)) === 0;
}

// Bit-reversal permutation plus butterfly stages.
export function fft(re, im) {
  const n = re.length;
  if (im.length !== n) throw new Error('fft: re/im length mismatch');
  if (!fftSizeSupported(n)) throw new Error(`fft: length ${n} is not a power of two`);

  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    while (j & bit) {
      j ^= bit;
      bit >>= 1;
    }
    j ^= bit;
    if (i < j) {
      const tr = re[i]; re[i] = re[j]; re[j] = tr;
      const ti = im[i]; im[i] = im[j]; im[j] = ti;
    }
  }

  for (let length = 2; length <= n; length <<= 1) {
    const half = length >> 1;
    const angle = (-2 * Math.PI) / length;
    const wRe = Math.cos(angle);
    const wIm = Math.sin(angle);
    for (let start = 0; start < n; start += length) {
      let curRe = 1;
      let curIm = 0;
      for (let k = 0; k < half; k++) {
        const a = start + k;
        const b = a + half;
        const tRe = curRe * re[b] - curIm * im[b];
        const tIm = curRe * im[b] + curIm * re[b];
        re[b] = re[a] - tRe;
        im[b] = im[a] - tIm;
        re[a] += tRe;
        im[a] += tIm;
        const nextRe = curRe * wRe - curIm * wIm;
        curIm = curRe * wIm + curIm * wRe;
        curRe = nextRe;
      }
    }
  }
  return { re, im };
}

// One-shot helper: copies the inputs so callers can reuse their buffers.
export function fftCopy(inputRe, inputIm) {
  const n = inputRe.length;
  const re = new Float64Array(n);
  const im = new Float64Array(n);
  re.set(inputRe);
  if (inputIm) im.set(inputIm);
  return fft(re, im);
}
