export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function mixToMono(channels, start = 0, end = channels[0]?.length ?? 0) {
  const length = Math.max(0, Math.floor(end - start));
  const mono = new Float32Array(length);
  if (!channels.length || !length) return mono;

  for (const channel of channels) {
    for (let i = 0; i < length; i += 1) {
      mono[i] += channel[start + i] / channels.length;
    }
  }
  return mono;
}

export function rms(data, start = 0, end = data.length) {
  const from = clamp(Math.floor(start), 0, data.length);
  const to = clamp(Math.floor(end), from, data.length);
  if (to <= from) return 0;

  let sum = 0;
  for (let i = from; i < to; i += 1) sum += data[i] * data[i];
  return Math.sqrt(sum / (to - from));
}

export function peak(data, start = 0, end = data.length) {
  const from = clamp(Math.floor(start), 0, data.length);
  const to = clamp(Math.floor(end), from, data.length);
  let value = 0;
  for (let i = from; i < to; i += 1) value = Math.max(value, Math.abs(data[i]));
  return value;
}

export function amplitudeToDb(value) {
  return 20 * Math.log10(Math.max(1e-9, Math.abs(value)));
}

export function nextPowerOfTwo(value) {
  let size = 1;
  while (size < value) size *= 2;
  return size;
}

export function fftReal(samples) {
  const n = nextPowerOfTwo(samples.length);
  const real = new Float64Array(n);
  const imag = new Float64Array(n);
  real.set(samples.subarray(0, Math.min(samples.length, n)));

  for (let i = 1, j = 0; i < n; i += 1) {
    let bit = n >> 1;
    while (j & bit) {
      j ^= bit;
      bit >>= 1;
    }
    j ^= bit;
    if (i < j) {
      [real[i], real[j]] = [real[j], real[i]];
      [imag[i], imag[j]] = [imag[j], imag[i]];
    }
  }

  for (let size = 2; size <= n; size *= 2) {
    const half = size / 2;
    const angle = (-2 * Math.PI) / size;
    const stepReal = Math.cos(angle);
    const stepImag = Math.sin(angle);
    for (let offset = 0; offset < n; offset += size) {
      let twiddleReal = 1;
      let twiddleImag = 0;
      for (let j = 0; j < half; j += 1) {
        const even = offset + j;
        const odd = even + half;
        const oddReal = real[odd] * twiddleReal - imag[odd] * twiddleImag;
        const oddImag = real[odd] * twiddleImag + imag[odd] * twiddleReal;
        real[odd] = real[even] - oddReal;
        imag[odd] = imag[even] - oddImag;
        real[even] += oddReal;
        imag[even] += oddImag;
        const nextReal = twiddleReal * stepReal - twiddleImag * stepImag;
        twiddleImag = twiddleReal * stepImag + twiddleImag * stepReal;
        twiddleReal = nextReal;
      }
    }
  }

  return { real, imag, size: n };
}

export function analyzeSpectrum(data, sampleRate, windowStart, windowSize = 2048) {
  const size = clamp(nextPowerOfTwo(windowSize), 32, 8192);
  const start = clamp(Math.floor(windowStart), 0, Math.max(0, data.length - size));
  const samples = new Float32Array(size);
  let mean = 0;
  for (let i = 0; i < size; i += 1) mean += data[start + i] ?? 0;
  mean /= size;

  for (let i = 0; i < size; i += 1) {
    const hann = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (size - 1));
    samples[i] = ((data[start + i] ?? 0) - mean) * hann;
  }

  const spectrum = fftReal(samples);
  const bins = size / 2;
  const frequencies = new Float32Array(bins);
  const magnitudes = new Float32Array(bins);
  let total = 0;

  for (let i = 0; i < bins; i += 1) {
    frequencies[i] = (i * sampleRate) / size;
    magnitudes[i] = Math.hypot(spectrum.real[i], spectrum.imag[i]) / size;
    total += magnitudes[i];
  }

  let dominantBin = 1;
  for (let i = 2; i < bins; i += 1) {
    if (magnitudes[i] > magnitudes[dominantBin]) dominantBin = i;
  }

  let weighted = 0;
  let weight = 0;
  for (let i = 1; i < bins; i += 1) {
    weighted += frequencies[i] * magnitudes[i];
    weight += magnitudes[i];
  }

  return {
    frequencies,
    magnitudes,
    sampleRate,
    size,
    start,
    dominantFrequency: frequencies[dominantBin],
    centroid: weight > 0 ? weighted / weight : 0,
    totalMagnitude: total
  };
}

export function spectralDistance(a, b) {
  const bins = Math.min(a.magnitudes.length, b.magnitudes.length);
  let totalA = 0;
  let totalB = 0;
  let difference = 0;

  for (let i = 1; i < bins; i += 1) {
    const av = a.magnitudes[i];
    const bv = b.magnitudes[i];
    totalA += av;
    totalB += bv;
    difference += Math.abs(av - bv);
  }

  const normalizer = totalA + totalB;
  return normalizer > 0 ? clamp(difference / normalizer, 0, 1) : 0;
}

function transientRatioDb(data, edge, sampleRate, direction) {
  const shortSize = Math.max(8, Math.round(sampleRate * 0.006));
  const longSize = Math.max(shortSize * 2, Math.round(sampleRate * 0.06));
  let shortStart;
  let longStart;

  if (direction > 0) {
    shortStart = edge;
    longStart = edge;
  } else {
    shortStart = edge - shortSize;
    longStart = edge - longSize;
  }

  const shortRms = rms(data, shortStart, shortStart + shortSize);
  const longRms = rms(data, longStart, longStart + longSize);
  return amplitudeToDb(shortRms / Math.max(longRms, 1e-9));
}

export function analyzeBoundary(data, sampleRate, start, end) {
  const from = clamp(Math.round(start), 0, Math.max(0, data.length - 2));
  const to = clamp(Math.round(end), from + 2, data.length);
  const edgeSize = Math.max(1, Math.round(sampleRate * 0.01));
  const first = data[from];
  const last = data[to - 1];
  const previous = data[Math.max(0, to - 2)];
  const next = data[Math.min(data.length - 1, from + 1)];
  const sampleDelta = Math.abs(first - last);
  const incomingSlope = last - previous;
  const outgoingSlope = next - first;
  const slopeJump = Math.abs(outgoingSlope - incomingSlope);
  const startRms = rms(data, from, Math.min(to, from + edgeSize));
  const endRms = rms(data, Math.max(from, to - edgeSize), to);
  const localLevel = Math.max(startRms, endRms, 1e-9);

  return {
    startSample: from,
    endSample: to,
    first,
    last,
    sampleDelta,
    sampleDeltaDb: amplitudeToDb(sampleDelta),
    sampleDeltaRelativeDb: amplitudeToDb(sampleDelta / localLevel),
    slopeJump,
    slopeJumpDb: amplitudeToDb(slopeJump),
    startRms,
    endRms,
    rmsMismatchDb: amplitudeToDb(startRms / Math.max(endRms, 1e-9)),
    startTransientDb: transientRatioDb(data, from, sampleRate, 1),
    endTransientDb: transientRatioDb(data, to, sampleRate, -1)
  };
}

export function findNearestZeroCrossing(data, nearSample, radiusSamples) {
  const center = clamp(Math.round(nearSample), 1, data.length - 2);
  const radius = Math.max(1, Math.round(radiusSamples));
  let best = center;
  let bestScore = Infinity;

  for (let i = Math.max(1, center - radius); i <= Math.min(data.length - 2, center + radius); i += 1) {
    const crosses = data[i - 1] <= 0 !== data[i] <= 0;
    if (!crosses) continue;
    const score = Math.abs(i - center) / radius + Math.abs(data[i]) * 0.15;
    if (score < bestScore) {
      best = i;
      bestScore = score;
    }
  }

  return best;
}

export function detectOnsets(mono, sampleRate) {
  const decimation = Math.max(1, Math.floor(sampleRate / 16000));
  const analysisRate = sampleRate / decimation;
  const analysisLength = Math.floor(mono.length / decimation);
  const analysis = new Float32Array(analysisLength);
  if (decimation === 1) {
    analysis.set(mono.subarray(0, analysisLength));
  } else {
    for (let i = 0; i < analysisLength; i += 1) {
      let sum = 0;
      for (let j = 0; j < decimation; j += 1) sum += mono[i * decimation + j];
      analysis[i] = sum / decimation;
    }
  }

  const frameSize = 1024;
  const hop = 256;
  const frameCount = Math.max(1, Math.floor((analysis.length - frameSize) / hop) + 1);
  const flux = new Float32Array(frameCount);
  let previousMagnitudes = null;

  for (let frame = 0; frame < frameCount; frame += 1) {
    const offset = frame * hop;
    const samples = new Float32Array(frameSize);
    for (let i = 0; i < frameSize; i += 1) {
      const hann = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (frameSize - 1));
      samples[i] = (analysis[offset + i] ?? 0) * hann;
    }

    const spectrum = fftReal(samples);
    const magnitudes = new Float32Array(frameSize / 2);
    let total = 0;
    for (let i = 1; i < frameSize / 2; i += 1) {
      magnitudes[i] = Math.hypot(spectrum.real[i], spectrum.imag[i]);
      total += magnitudes[i];
    }

    if (previousMagnitudes) {
      let sum = 0;
      for (let i = 1; i < frameSize / 2; i += 1) {
        sum += Math.max(0, magnitudes[i] - previousMagnitudes[i]);
      }
      flux[frame] = sum / Math.max(total, 1e-9);
    }
    previousMagnitudes = magnitudes;
  }

  const sorted = Array.from(flux).sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length * 0.5)] ?? 0;
  const deviations = sorted.map((value) => Math.abs(value - median)).sort((a, b) => a - b);
  const mad = deviations[Math.floor(deviations.length * 0.5)] ?? 0;
  const threshold = median + Math.max(mad * 3.2, median * 0.35, 1e-6);
  const maximum = sorted[sorted.length - 1] ?? 1;
  const onsets = [];

  for (let i = 2; i < flux.length - 2; i += 1) {
    if (flux[i] < threshold) continue;
    if (flux[i] < flux[i - 1] || flux[i] < flux[i + 1]) continue;
    const time = (i * hop) / analysisRate;
    const strength = clamp((flux[i] - threshold) / Math.max(maximum - threshold, 1e-9), 0, 1);
    if (onsets.length && time - onsets[onsets.length - 1].time < 0.14) {
      if (strength > onsets[onsets.length - 1].strength) onsets[onsets.length - 1] = { time, strength };
      continue;
    }
    onsets.push({ time, strength });
  }

  const intervals = [];
  for (let i = 1; i < onsets.length; i += 1) {
    const interval = onsets[i].time - onsets[i - 1].time;
    if (interval >= 0.2 && interval <= 1.5) intervals.push(interval);
  }

  let bpm = 0;
  let confidence = 0;
  let medianInterval = 0;
  if (intervals.length) {
    const sortedIntervals = intervals.slice().sort((a, b) => a - b);
    const center = sortedIntervals[Math.floor(sortedIntervals.length / 2)];
    const inliers = intervals.filter((value) => Math.abs(value - center) <= Math.max(0.015, center * 0.15));
    const usable = inliers.length ? inliers : intervals;
    medianInterval = usable.reduce((sum, value) => sum + value, 0) / usable.length;
    const mean = medianInterval;
    const variance = usable.reduce((sum, value) => sum + (value - mean) ** 2, 0) / usable.length;
    const deviation = Math.sqrt(variance);
    confidence = clamp(1 - deviation / Math.max(mean, 1e-9), 0, 1);
    bpm = clamp(60 / medianInterval, 30, 240);
    if (bpm < 70) bpm *= 2;
    if (bpm > 200) bpm /= 2;
  }

  return { onsets, intervals, bpm, confidence, medianInterval };
}

export function prepareLoop(channels, startSample, endSample, fadeSamples) {
  if (!channels.length) throw new Error("没有可渲染的声道");
  const sourceLength = channels[0].length;
  const from = clamp(Math.round(startSample), 0, sourceLength - 3);
  const to = clamp(Math.round(endSample), from + 3, sourceLength);
  const cropLength = to - from;
  const fade = clamp(Math.round(fadeSamples), 0, Math.floor(cropLength / 2));
  const outputLength = cropLength - fade;
  if (outputLength < 2) throw new Error("裁切区域太短，无法生成循环");

  const rendered = channels.map((source) => {
    const output = new Float32Array(outputLength);
    if (fade > 0) {
      for (let i = 0; i < fade; i += 1) {
        const t = fade === 1 ? 0 : i / (fade - 1);
        const fadeIn = Math.sin((Math.PI / 2) * t);
        const fadeOut = Math.cos((Math.PI / 2) * t);
        output[i] = source[from + i] * fadeIn + source[to - fade + i] * fadeOut;
      }
    }
    for (let i = fade; i < outputLength; i += 1) output[i] = source[from + i];
    return output;
  });

  return {
    channels: rendered,
    length: outputLength,
    cropLength,
    fadeSamples: fade,
    startSample: from,
    endSample: to,
    globallyAttenuated: false
  };
}

export function measureLoop(loopChannels, sourceChannels, cropStart, cropEnd, sampleRate) {
  const mono = mixToMono(loopChannels);
  const source = mixToMono(sourceChannels);
  const length = mono.length;
  if (length < 2) throw new Error("循环缓冲没有足够采样点");

  const first = mono[0];
  const last = mono[length - 1];
  const previous = mono[Math.max(0, length - 2)];
  const next = mono[Math.min(length - 1, 1)];
  const sampleJump = Math.abs(first - last);
  const incomingStep = Math.abs(last - previous);
  const outgoingStep = Math.abs(next - first);
  const localStep = Math.max(incomingStep, outgoingStep, 1e-9);
  const stepExcess = Math.max(0, sampleJump - localStep);
  const stepRatio = sampleJump / localStep;
  const slopeJump = Math.abs(next - first - (last - previous));
  const loopRms = rms(mono);
  const cropRms = rms(source, cropStart, cropEnd);
  const rmsRetention = loopRms / Math.max(cropRms, 1e-9);
  const loopPeak = peak(mono);

  return {
    sampleJump,
    sampleJumpDb: amplitudeToDb(sampleJump),
    stepExcess,
    stepExcessDb: amplitudeToDb(stepExcess),
    stepRatio,
    incomingStep,
    outgoingStep,
    slopeJump,
    slopeJumpDb: amplitudeToDb(slopeJump),
    rmsRetention,
    loopRms,
    cropRms,
    peak: loopPeak,
    duration: length / sampleRate,
    samples: length,
    playable: Number.isFinite(loopRms) && loopPeak > 1e-7 && loopRms > 1e-6,
    globallyAttenuated: false
  };
}

export function auditTenRounds(loopChannels, sourceChannels, cropStart, cropEnd, sampleRate) {
  const measurement = measureLoop(loopChannels, sourceChannels, cropStart, cropEnd, sampleRate);
  return Array.from({ length: 10 }, (_, index) => ({
    round: index + 1,
    ...measurement
  }));
}
