// Deterministic test signals used by both the UI and the headless benchmarks.
// Every generator returns { sampleRate, samples (Float64Array), meta }.

function allocate(durationSec, sampleRate) {
  const length = Math.round(durationSec * sampleRate);
  return { length, samples: new Float64Array(length) };
}

// Two linear chirps that cross once in the middle of the segment.
// Up-chirp f0 -> f1, down-chirp f1 -> f0, each with its own amplitude.
export function crossingChirps(opts = {}) {
  const sampleRate = opts.sampleRate ?? 16000;
  const durationSec = opts.durationSec ?? 1.0;
  const f0 = opts.f0 ?? 1000;
  const f1 = opts.f1 ?? 4000;
  const ampUp = opts.ampUp ?? 0.6;
  const ampDown = opts.ampDown ?? 0.6;
  const { length, samples } = allocate(durationSec, sampleRate);
  const k = (f1 - f0) / durationSec;
  for (let n = 0; n < length; n++) {
    const t = n / sampleRate;
    const phaseUp = 2 * Math.PI * (f0 * t + 0.5 * k * t * t);
    const phaseDown = 2 * Math.PI * (f1 * t - 0.5 * k * t * t);
    samples[n] = ampUp * Math.sin(phaseUp) + ampDown * Math.sin(phaseDown);
  }
  return {
    sampleRate,
    samples,
    meta: {
      name: 'crossing-chirps',
      durationSec,
      instantFreqUp: (t) => f0 + k * t,
      instantFreqDown: (t) => f1 - k * t,
      crossTime: durationSec / 2,
      crossFreq: (f0 + f1) / 2
    }
  };
}

// Two steady pure tones separated by a small delta, both well below Nyquist.
export function closePureTones(opts = {}) {
  const sampleRate = opts.sampleRate ?? 16000;
  const durationSec = opts.durationSec ?? 1.0;
  const fA = opts.fA ?? 2000;
  const fB = opts.fB ?? 2250;
  const ampA = opts.ampA ?? 0.7;
  const ampB = opts.ampB ?? 0.5;
  const { length, samples } = allocate(durationSec, sampleRate);
  for (let n = 0; n < length; n++) {
    const t = n / sampleRate;
    samples[n] =
      ampA * Math.sin(2 * Math.PI * fA * t) +
      ampB * Math.sin(2 * Math.PI * fB * t);
  }
  return {
    sampleRate,
    samples,
    meta: { name: 'close-pure-tones', durationSec, tones: [{ f: fA, amp: ampA }, { f: fB, amp: ampB }] }
  };
}

// A short Gaussian-enveloped tone burst: sharp onset/offset, single carrier.
// Onset reference time is the burst center; sigmaSec controls its width.
export function transientBurst(opts = {}) {
  const sampleRate = opts.sampleRate ?? 16000;
  const durationSec = opts.durationSec ?? 0.5;
  const centerSec = opts.centerSec ?? 0.25;
  const sigmaSec = opts.sigmaSec ?? 0.004;
  const carrier = opts.carrier ?? 3000;
  const amp = opts.amp ?? 0.9;
  const { length, samples } = allocate(durationSec, sampleRate);
  for (let n = 0; n < length; n++) {
    const t = n / sampleRate;
    const g = Math.exp(-0.5 * ((t - centerSec) / sigmaSec) ** 2);
    samples[n] = amp * g * Math.sin(2 * Math.PI * carrier * t);
  }
  return {
    sampleRate,
    samples,
    meta: {
      name: 'transient-burst',
      durationSec,
      centerSec,
      sigmaSec,
      carrier,
      // Onset is conventionally reported where the envelope reaches half max.
      onsetSec: centerSec - sigmaSec * Math.sqrt(2 * Math.log(2))
    }
  };
}

export const SIGNAL_BUILDERS = {
  'crossing-chirps': crossingChirps,
  'close-pure-tones': closePureTones,
  'transient-burst': transientBurst
};
