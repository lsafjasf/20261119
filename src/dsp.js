export const SAMPLE_RATE = 48000;
export const ANALYSIS_VERSION = '1.0';

export const FIR_PRESETS = {
  studio: {
    id: 'studio',
    label: '近场监听室',
    directMs: 5.5,
    reflections: [
      { offsetMs: 7.5, gain: 0.46 },
      { offsetMs: 17.0, gain: -0.31 },
      { offsetMs: 29.0, gain: 0.18 },
      { offsetMs: 43.0, gain: 0.10 },
      { offsetMs: 62.0, gain: 0.06 },
    ],
  },
  hall: {
    id: 'hall',
    label: '小型混响厅',
    directMs: 12.0,
    reflections: [
      { offsetMs: 18.0, gain: 0.38 },
      { offsetMs: 31.0, gain: -0.27 },
      { offsetMs: 49.0, gain: 0.22 },
      { offsetMs: 74.0, gain: 0.13 },
      { offsetMs: 106.0, gain: -0.08 },
    ],
  },
};

export function nextPowerOfTwo(value) {
  return 2 ** Math.ceil(Math.log2(Math.max(1, value)));
}

export function fft(real, imag, inverse = false) {
  const size = real.length;
  if ((size & (size - 1)) !== 0) {
    throw new Error('FFT size must be a power of two');
  }

  for (let i = 1, j = 0; i < size; i++) {
    let bit = size >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [real[i], real[j]] = [real[j], real[i]];
      [imag[i], imag[j]] = [imag[j], imag[i]];
    }
  }

  for (let length = 2; length <= size; length <<= 1) {
    const angle = (inverse ? 2 : -2) * Math.PI / length;
    const stepReal = Math.cos(angle);
    const stepImag = Math.sin(angle);
    const half = length >> 1;
    for (let start = 0; start < size; start += length) {
      let twiddleReal = 1;
      let twiddleImag = 0;
      for (let offset = 0; offset < half; offset++) {
        const a = start + offset;
        const b = a + half;
        const productReal = real[b] * twiddleReal - imag[b] * twiddleImag;
        const productImag = real[b] * twiddleImag + imag[b] * twiddleReal;
        real[b] = real[a] - productReal;
        imag[b] = imag[a] - productImag;
        real[a] += productReal;
        imag[a] += productImag;
        const nextReal = twiddleReal * stepReal - twiddleImag * stepImag;
        twiddleImag = twiddleReal * stepImag + twiddleImag * stepReal;
        twiddleReal = nextReal;
      }
    }
  }

  if (inverse) {
    for (let i = 0; i < size; i++) {
      real[i] /= size;
      imag[i] /= size;
    }
  }
}

export function linearConvolve(left, right) {
  const outputLength = left.length + right.length - 1;
  const size = nextPowerOfTwo(outputLength);
  const leftReal = new Float64Array(size);
  const leftImag = new Float64Array(size);
  const rightReal = new Float64Array(size);
  const rightImag = new Float64Array(size);
  leftReal.set(left);
  rightReal.set(right);

  fft(leftReal, leftImag);
  fft(rightReal, rightImag);
  for (let i = 0; i < size; i++) {
    const real = leftReal[i] * rightReal[i] - leftImag[i] * rightImag[i];
    const imag = leftReal[i] * rightImag[i] + leftImag[i] * rightReal[i];
    leftReal[i] = real;
    leftImag[i] = imag;
  }
  fft(leftReal, leftImag, true);
  return leftReal.slice(0, outputLength);
}

function tukeyFade(samples, sampleRate, fadeMs) {
  const fadeLength = Math.min(
    Math.floor(samples.length / 2),
    Math.max(1, Math.round(sampleRate * fadeMs / 1000)),
  );
  for (let i = 0; i < fadeLength; i++) {
    const phase = 0.5 - 0.5 * Math.cos(Math.PI * i / fadeLength);
    samples[i] *= phase;
    samples[samples.length - 1 - i] *= phase;
  }
}

export function createLogSweep({
  sampleRate = SAMPLE_RATE,
  f1 = 80,
  f2 = 20000,
  duration = 1.2,
  fadeMs = 3,
} = {}) {
  const length = Math.max(2, Math.round(duration * sampleRate));
  const actualDuration = length / sampleRate;
  const ratio = f2 / f1;
  const tau = Math.log(ratio) / actualDuration;
  const samples = new Float64Array(length);
  const phase = new Float64Array(length);
  const frequency = new Float64Array(length);

  for (let i = 0; i < length; i++) {
    const time = i / sampleRate;
    const instantaneous = f1 * Math.exp(tau * time);
    frequency[i] = instantaneous;
    phase[i] = 2 * Math.PI * f1 / tau * (Math.exp(tau * time) - 1);
    samples[i] = Math.sin(phase[i]);
  }
  tukeyFade(samples, sampleRate, fadeMs);

  return {
    samples,
    phase,
    frequency,
    sampleRate,
    f1,
    f2,
    duration: actualDuration,
    tau,
    timeBandwidth: actualDuration * Math.log2(ratio),
  };
}

export function buildKnownFIR(presetId = 'studio', sampleRate = SAMPLE_RATE) {
  const preset = FIR_PRESETS[presetId] ?? FIR_PRESETS.studio;
  const directIndex = Math.round(preset.directMs * sampleRate / 1000);
  const tapSpecs = [
    { offsetMs: 0, gain: 1, kind: 'direct' },
    ...preset.reflections.map((tap) => ({ ...tap, kind: 'reflection' })),
  ];
  const taps = tapSpecs.map((tap) => ({
    ...tap,
    index: directIndex + Math.round(tap.offsetMs * sampleRate / 1000),
    timeMs: preset.directMs + tap.offsetMs,
  }));
  const length = taps[taps.length - 1].index + 1;
  const samples = new Float64Array(length);
  for (const tap of taps) samples[tap.index] += tap.gain;

  return {
    preset,
    presetId: preset.id,
    samples,
    taps,
    directIndex,
    directTimeMs: preset.directMs,
    spanMs: taps[taps.length - 1].offsetMs,
    spanSamples: taps[taps.length - 1].index - directIndex,
  };
}

export function createInverseFilter(sweep, { regularize = 0 } = {}) {
  const { samples, sampleRate, f2, tau, duration } = sweep;
  const length = samples.length;
  const raw = new Float64Array(length);
  let calibration = 0;

  for (let i = 0; i < length; i++) {
    const frequency = f2 * Math.exp(-tau * i / sampleRate);
    const value = samples[length - 1 - i] * frequency;
    raw[i] = value;
    calibration += samples[i] ** 2 * frequency;
  }

  calibration = Math.max(calibration, Number.EPSILON);
  const samplesOut = new Float64Array(length);
  let peak = 0;
  for (let i = 0; i < length; i++) {
    samplesOut[i] = raw[i] / calibration;
    peak = Math.max(peak, Math.abs(samplesOut[i]));
  }

  return {
    samples: samplesOut,
    raw,
    calibration,
    peak,
    compensationDbPerOctave: 6.0206,
    duration,
    sampleRate,
    regularize,
  };
}

function deconvolutionResponseGain(sweep, inverse, referenceFrequency) {
  const sweepSum = { real: 0, imag: 0 };
  const inverseSum = { real: 0, imag: 0 };
  const step = 2 * Math.PI * referenceFrequency / sweep.sampleRate;
  for (let i = 0; i < sweep.samples.length; i++) {
    const phase = step * i;
    const cosine = Math.cos(phase);
    const sine = Math.sin(phase);
    sweepSum.real += sweep.samples[i] * cosine;
    sweepSum.imag -= sweep.samples[i] * sine;
    inverseSum.real += inverse.samples[i] * cosine;
    inverseSum.imag -= inverse.samples[i] * sine;
  }
  const real = sweepSum.real * inverseSum.real - sweepSum.imag * inverseSum.imag;
  const imag = sweepSum.real * inverseSum.imag + sweepSum.imag * inverseSum.real;
  return Math.max(1e-12, Math.hypot(real, imag));
}

function findPeak(samples, start = 0, end = samples.length) {
  let index = start;
  let value = 0;
  for (let i = Math.max(0, start); i < Math.min(samples.length, end); i++) {
    const magnitude = Math.abs(samples[i]);
    if (magnitude > value) {
      value = magnitude;
      index = i;
    }
  }
  return { index, value };
}

function sumSquares(samples, start, end) {
  let sum = 0;
  const from = Math.max(0, Math.floor(start));
  const to = Math.min(samples.length, Math.ceil(end));
  for (let i = from; i < to; i++) sum += samples[i] ** 2;
  return sum;
}

function maxAbs(samples, start, end) {
  let peak = 0;
  const from = Math.max(0, Math.floor(start));
  const to = Math.min(samples.length, Math.ceil(end));
  for (let i = from; i < to; i++) peak = Math.max(peak, Math.abs(samples[i]));
  return peak;
}

function windowSegment(samples, start, end, sampleRate, fadeMs = 1.5) {
  const length = Math.max(1, Math.floor(end) - Math.floor(start));
  const result = new Float64Array(length);
  const fade = Math.min(
    Math.floor(length / 2),
    Math.max(1, Math.round(sampleRate * fadeMs / 1000)),
  );
  for (let i = 0; i < length; i++) {
    let weight = 1;
    if (i < fade) weight = 0.5 - 0.5 * Math.cos(Math.PI * i / fade);
    if (i >= length - fade) {
      const tail = length - 1 - i;
      weight = Math.min(weight, 0.5 - 0.5 * Math.cos(Math.PI * tail / fade));
    }
    result[i] = samples[Math.floor(start) + i] * weight;
  }
  return result;
}

function spectrum(samples, size) {
  const real = new Float64Array(size);
  const imag = new Float64Array(size);
  real.set(samples.subarray(0, Math.min(samples.length, size)));
  fft(real, imag);
  return { real, imag };
}

function spectrumMagnitudeAt(spectrumValue, frequency, sampleRate, size) {
  const position = frequency * size / sampleRate;
  const lower = Math.max(0, Math.floor(position));
  const upper = Math.min(size - 1, lower + 1);
  const fraction = position - lower;
  const lowerMagnitude = Math.hypot(
    spectrumValue.real[lower],
    spectrumValue.imag[lower],
  );
  const upperMagnitude = Math.hypot(
    spectrumValue.real[upper],
    spectrumValue.imag[upper],
  );
  return lowerMagnitude + fraction * (upperMagnitude - lowerMagnitude);
}

function smoothedDbAt(spectrumValue, frequency, sampleRate, size, fraction = 12) {
  const low = frequency * 2 ** (-0.5 / fraction);
  const high = frequency * 2 ** (0.5 / fraction);
  const firstBin = Math.max(1, Math.ceil(low * size / sampleRate));
  const lastBin = Math.min(size / 2 - 1, Math.floor(high * size / sampleRate));
  if (lastBin < firstBin) {
    return 20 * Math.log10(Math.max(1e-12, spectrumMagnitudeAt(
      spectrumValue,
      frequency,
      sampleRate,
      size,
    )));
  }

  let power = 0;
  for (let i = firstBin; i <= lastBin; i++) {
    const magnitude = Math.hypot(spectrumValue.real[i], spectrumValue.imag[i]);
    power += magnitude * magnitude;
  }
  power /= lastBin - firstBin + 1;
  return 10 * Math.log10(Math.max(1e-24, power));
}

function buildMagnitudeComparison(recoveredIR, trueFIR, band, fftSize, sampleRate) {
  const recoveredSpectrum = spectrum(recoveredIR, fftSize);
  const trueSpectrum = spectrum(trueFIR.samples, fftSize);
  const curve = [];
  const points = 260;

  for (let i = 0; i < points; i++) {
    const frequency = band.f1 * (band.f2 / band.f1) ** (i / (points - 1));
    const trueDb = smoothedDbAt(trueSpectrum, frequency, sampleRate, fftSize);
    const recoveredDb = smoothedDbAt(
      recoveredSpectrum,
      frequency,
      sampleRate,
      fftSize,
    );
    curve.push({
      frequency,
      trueDb,
      recoveredDb,
      errorDb: recoveredDb - trueDb,
    });
  }

  const compareFrom = Math.max(band.f1 * 1.08, 50);
  const compareTo = Math.min(band.f2 * 0.92, sampleRate * 0.47);
  let errorSquare = 0;
  let maxError = 0;
  let compared = 0;
  for (let i = 1; i < 240; i++) {
    const frequency = compareFrom * (compareTo / compareFrom) ** (i / 240);
    const trueDb = smoothedDbAt(trueSpectrum, frequency, sampleRate, fftSize);
    const recoveredDb = smoothedDbAt(
      recoveredSpectrum,
      frequency,
      sampleRate,
      fftSize,
    );
    const error = recoveredDb - trueDb;
    errorSquare += error * error;
    maxError = Math.max(maxError, Math.abs(error));
    compared++;
  }

  return {
    curve,
    fftSize,
    compareFrom,
    compareTo,
    rmsDb: Math.sqrt(errorSquare / Math.max(1, compared)),
    maxErrorDb: maxError,
    smoothingFraction: 12,
  };
}

function relativeMs(sampleIndex, referenceIndex, sampleRate) {
  return (sampleIndex - referenceIndex) * 1000 / sampleRate;
}

export function analyzeSweep(options = {}) {
  const startedAt = performance.now();
  const sampleRate = SAMPLE_RATE;
  const f1 = Math.max(20, Number(options.f1 ?? 80));
  const f2 = Math.min(sampleRate * 0.47, Math.max(f1 + 20, Number(options.f2 ?? 20000)));
  const duration = Math.min(2.4, Math.max(0.55, Number(options.duration ?? 1.2)));
  const nonlinear = Boolean(options.nonlinear);
  const k2 = nonlinear ? Math.max(0, Number(options.k2 ?? 0.08)) : 0;
  const presetId = options.presetId ?? 'studio';

  const sweep = createLogSweep({ sampleRate, f1, f2, duration });
  const inverse = createInverseFilter(sweep);
  const fir = buildKnownFIR(presetId, sampleRate);
  const linearOutput = linearConvolve(sweep.samples, fir.samples);
  const nonlinearInput = nonlinear
    ? Float64Array.from(sweep.samples, (value) => value + k2 * value * value)
    : sweep.samples;
  const measuredOutput = nonlinear
    ? linearConvolve(nonlinearInput, fir.samples)
    : linearOutput;

  const linearIR = linearConvolve(linearOutput, inverse.samples);
  const measuredIR = nonlinear
    ? linearConvolve(measuredOutput, inverse.samples)
    : linearIR;
  const responseCalibration = deconvolutionResponseGain(
    sweep,
    inverse,
    Math.sqrt(f1 * f2),
  );
  for (let i = 0; i < linearIR.length; i++) {
    linearIR[i] /= responseCalibration;
    if (measuredIR !== linearIR) measuredIR[i] /= responseCalibration;
  }
  inverse.responseCalibration = responseCalibration;
  const correlationIndex = sweep.samples.length - 1;
  const mainPeak = findPeak(measuredIR);
  const roomGate = {
    start: Math.max(0, mainPeak.index - Math.round(0.008 * sampleRate)),
    end: Math.min(
      measuredIR.length,
      mainPeak.index + fir.spanSamples + Math.round(0.008 * sampleRate),
    ),
  };

  const distortionIR = new Float64Array(measuredIR.length);
  for (let i = 0; i < distortionIR.length; i++) {
    distortionIR[i] = measuredIR[i] - linearIR[i];
  }

  const delta2Samples = Math.round(
    Math.log(2) / Math.log(f2 / f1) * sweep.duration * sampleRate,
  );
  const h2Anchor = mainPeak.index - delta2Samples;
  const h2Window = {
    start: Math.max(0, h2Anchor - Math.round(0.006 * sampleRate)),
    end: Math.min(
      measuredIR.length,
      h2Anchor + fir.spanSamples + Math.round(0.006 * sampleRate),
    ),
    anchor: h2Anchor,
  };
  const h2Peak = findPeak(distortionIR, h2Window.start, h2Window.end);
  const h1Energy = sumSquares(linearIR, roomGate.start, roomGate.end);
  const h2Energy = sumSquares(distortionIR, h2Window.start, h2Window.end);
  const h2LeakEnergy = sumSquares(distortionIR, roomGate.start, roomGate.end);
  const linearLeakEnergy = sumSquares(linearIR, h2Window.start, h2Window.end);
  const h2LeakPeak = maxAbs(distortionIR, roomGate.start, roomGate.end);
  const linearLeakPeak = maxAbs(linearIR, h2Window.start, h2Window.end);
  const ratioToDb = (numerator, denominator) =>
    10 * Math.log10((numerator + 1e-22) / (denominator + 1e-22));
  const amplitudeToDb = (numerator, denominator) =>
    20 * Math.log10((numerator + 1e-16) / (denominator + 1e-16));

  const responseFFTSize = nextPowerOfTwo(Math.max(
    16384,
    (roomGate.end - roomGate.start) * 4,
  ));
  const recoveredRoomIR = windowSegment(
    measuredIR,
    roomGate.start,
    roomGate.end,
    sampleRate,
  );
  const response = buildMagnitudeComparison(
    recoveredRoomIR,
    fir,
    { f1, f2 },
    responseFFTSize,
    sampleRate,
  );
  const truePeakDelayMs = fir.directTimeMs;
  const recoveredPeakDelayMs = (mainPeak.index - correlationIndex) * 1000 / sampleRate;
  const separationMarginMs = (
    roomGate.start - h2Window.end
  ) * 1000 / sampleRate;

  return {
    version: ANALYSIS_VERSION,
    sampleRate,
    parameters: { f1, f2, duration: sweep.duration, nonlinear, k2, presetId },
    sweep,
    inverse,
    fir,
    nonlinearInput,
    linearOutput,
    measuredOutput,
    linearIR,
    measuredIR,
    distortionIR,
    mainPeak: {
      index: mainPeak.index,
      value: mainPeak.value,
      delayMs: recoveredPeakDelayMs,
      relativeIndex: mainPeak.index - correlationIndex,
    },
    roomGate,
    h2: {
      anchor: h2Anchor,
      window: h2Window,
      peakIndex: h2Peak.index,
      peakValue: h2Peak.value,
      peakDelayMs: relativeMs(h2Peak.index, mainPeak.index, sampleRate),
      deltaSamples: delta2Samples,
      deltaMs: delta2Samples * 1000 / sampleRate,
      energy: h2Energy,
      relativeToH1Db: ratioToDb(h2Energy, h1Energy),
      leakageIntoRoomDb: ratioToDb(h2LeakEnergy, h2Energy),
      linearLeakageIntoWindowDb: ratioToDb(linearLeakEnergy, h2Energy),
      leakagePeakIntoRoomDb: amplitudeToDb(h2LeakPeak, h2Peak.value),
      linearLeakagePeakIntoWindowDb: amplitudeToDb(linearLeakPeak, h2Peak.value),
    },
    metrics: {
      truePeakDelayMs,
      recoveredPeakDelayMs,
      peakDelayErrorMs: recoveredPeakDelayMs - truePeakDelayMs,
      h1Energy,
      h2Energy,
      h2RelativeDb: ratioToDb(h2Energy, h1Energy),
      h2LeakageIntoRoomDb: ratioToDb(h2LeakEnergy, h2Energy),
      linearLeakageIntoH2Db: ratioToDb(linearLeakEnergy, h2Energy),
      h2LeakagePeakDb: amplitudeToDb(h2LeakPeak, h2Peak.value),
      linearLeakagePeakDb: amplitudeToDb(linearLeakPeak, h2Peak.value),
      separationMarginMs,
      magnitudeRmsDb: response.rmsDb,
      magnitudeMaxErrorDb: response.maxErrorDb,
    },
    response,
    windows: {
      mainStartMs: relativeMs(roomGate.start, mainPeak.index, sampleRate),
      mainEndMs: relativeMs(roomGate.end, mainPeak.index, sampleRate),
      h2StartMs: relativeMs(h2Window.start, mainPeak.index, sampleRate),
      h2EndMs: relativeMs(h2Window.end, mainPeak.index, sampleRate),
    },
    analysisMs: performance.now() - startedAt,
  };
}
