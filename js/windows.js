// Analysis windows and their discrete derivatives.
// Windows are indexed over n = 0..N-1 and centered conceptually at (N-1)/2,
// which matches the centered STFT convention used by the reassignment core.

export function hannWindow(n) {
  const w = new Float64Array(n);
  if (n === 1) {
    w[0] = 1;
    return w;
  }
  for (let i = 0; i < n; i++) {
    w[i] = 0.5 * (1 - Math.cos((2 * Math.PI * i) / (n - 1)));
  }
  return w;
}

// Symmetric periodic Hann variant (zero at both ends, common in STFT use).
export function periodicHannWindow(n) {
  const w = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    w[i] = 0.5 * (1 - Math.cos((2 * Math.PI * i) / n));
  }
  return w;
}

// Discrete first derivative w'[n] in units of 1/sample.
// Central differences in the interior, one-sided differences at the edges.
export function windowDerivative(w) {
  const n = w.length;
  const d = new Float64Array(n);
  if (n < 2) return d;
  d[0] = w[1] - w[0];
  for (let i = 1; i < n - 1; i++) {
    d[i] = 0.5 * (w[i + 1] - w[i - 1]);
  }
  d[n - 1] = w[n - 1] - w[n - 2];
  return d;
}

// Time-weighted window t*w(t) with t centered at (N-1)/2, in samples.
export function timeWeightedWindow(w) {
  const n = w.length;
  const center = (n - 1) / 2;
  const tw = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    tw[i] = (i - center) * w[i];
  }
  return tw;
}

// Normalized window energy, useful for sanity checks.
export function windowEnergy(w) {
  let e = 0;
  for (let i = 0; i < w.length; i++) e += w[i] * w[i];
  return e;
}
