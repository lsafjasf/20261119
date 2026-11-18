export const TAU = Math.PI * 2;
export const DEG = Math.PI / 180;

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function angularDistance(a, b) {
  const delta = Math.abs(((a - b + 180) % 360 + 360) % 360 - 180);
  return delta;
}

export function normalizeDegrees(angle) {
  return ((angle % 360) + 360) % 360;
}

export function makeCircularArray(count = 8, radius = 0.12, startAngleDeg = 0) {
  return Array.from({ length: count }, (_, index) => {
    const angleDeg = normalizeDegrees(startAngleDeg + index * 360 / count);
    const phi = angleDeg * DEG;
    return {
      id: `CH${index + 1}`,
      index,
      x: radius * Math.cos(phi),
      y: radius * Math.sin(phi),
      angleDeg,
      radius
    };
  });
}

function mulberry32(seed) {
  let state = seed >>> 0;
  return () => {
    state += 0x6d2b79f5;
    let value = state;
    value = Math.imul(value ^ value >>> 15, value | 1);
    value ^= value + Math.imul(value ^ value >>> 7, value | 61);
    return ((value ^ value >>> 14) >>> 0) / 4294967296;
  };
}

function sampleLinear(wave, position) {
  if (position <= 0 || position >= wave.length - 1) return 0;
  const index = position | 0;
  const fraction = position - index;
  return wave[index] + fraction * (wave[index + 1] - wave[index]);
}

function makeSourceWave(sampleRate, duration, seed, profile = {}) {
  const length = Math.ceil(sampleRate * duration);
  const wave = new Float32Array(length);
  const random = mulberry32(seed);
  const base = profile.baseFrequency ?? 620;
  const spread = profile.spread ?? 1;
  let fastNoise = 0;
  let slowNoise = 0;
  let previousWhite = 0;

  for (let index = 0; index < length; index += 1) {
    const time = index / sampleRate;
    const white = random() * 2 - 1;
    fastNoise += 0.34 * (white - fastNoise);
    slowNoise += 0.035 * (white - slowNoise);
    const bandNoise = fastNoise - slowNoise;
    const noiseEdge = previousWhite - white;
    previousWhite = white;

    const glide = 0.012 * Math.sin(TAU * 0.23 * time);
    const phase = TAU * (base * time * (1 + glide) + 0.5 * spread * 33 * time * time);
    const modulationA = 0.58 + 0.24 * Math.sin(TAU * 1.7 * time + seed * 0.07);
    const modulationB = 0.54 + 0.22 * Math.sin(TAU * 2.9 * time + 0.8);
    const harmonic = Math.sin(phase * 1.5 + 0.5 * Math.sin(TAU * 0.9 * time));
    const tonal = Math.sin(phase) * modulationA
      + 0.43 * Math.sin(phase * 2.02) * modulationB
      + 0.2 * harmonic;
    wave[index] = 0.52 * tonal + 0.32 * bandNoise + 0.08 * noiseEdge * spread;
  }

  let peak = 0;
  for (let index = 0; index < length; index += 1) {
    peak = Math.max(peak, Math.abs(wave[index]));
  }
  const scale = peak > 1e-8 ? 0.62 / peak : 1;
  const fadeSamples = Math.floor(sampleRate * 0.025);
  for (let index = 0; index < length; index += 1) {
    const fadeIn = Math.min(1, index / fadeSamples);
    const fadeOut = Math.min(1, (length - 1 - index) / fadeSamples);
    wave[index] *= scale * Math.min(fadeIn, fadeOut);
  }
  return wave;
}

function makeNoiseWave(sampleRate, duration, seed) {
  const random = mulberry32(seed);
  const length = Math.ceil(sampleRate * duration);
  const wave = new Float32Array(length);
  let low = 0;
  for (let index = 0; index < length; index += 1) {
    const white = random() * 2 - 1;
    low += 0.18 * (white - low);
    wave[index] = low * 0.6;
  }
  return wave;
}

function addFractionalWave(wave, target, offsetSamples, gain) {
  if (!gain) return;
  for (let index = 0; index < target.length; index += 1) {
    target[index] += gain * sampleLinear(wave, index - offsetSamples);
  }
}

export function synthesizeArray({
  array,
  sources,
  speedOfSound = 343,
  sampleRate = 16000,
  duration = 2.4,
  reverb = 0.38,
  ambient = 0.025
}) {
  const channels = array.map(() => new Float32Array(Math.ceil(sampleRate * duration)));
  const preDelay = 0.045;
  const reflectionDirections = [47, -73, 129, -161, 24, -106];
  const reflectionDelays = [0.021, 0.034, 0.052, 0.071, 0.087, 0.108];
  const reflectionGains = [0.34, 0.26, 0.2, 0.155, 0.12, 0.09];

  sources.forEach((source, sourceIndex) => {
    const wave = makeSourceWave(sampleRate, duration, source.seed ?? 1009 + sourceIndex * 313, {
      baseFrequency: source.baseFrequency ?? 590 + sourceIndex * 270,
      spread: source.spread ?? 1
    });
    const directGain = source.level ?? 0.72;
    const components = [{
      direction: source.angleDeg,
      offset: preDelay,
      gain: directGain
    }];

    if (reverb > 0.001) {
      reflectionDirections.forEach((offset, reflectionIndex) => {
        components.push({
          direction: normalizeDegrees(source.angleDeg + offset + sourceIndex * 11),
          offset: preDelay + reflectionDelays[reflectionIndex],
          gain: directGain * reverb * reflectionGains[reflectionIndex]
        });
      });
    }

    components.forEach((component) => {
      const phi = component.direction * DEG;
      array.forEach((microphone, channelIndex) => {
        const projection = microphone.x * Math.cos(phi) + microphone.y * Math.sin(phi);
        const arrivalSamples = (component.offset + projection / speedOfSound) * sampleRate;
        addFractionalWave(wave, channels[channelIndex], arrivalSamples, component.gain);
      });
    });
  });

  if (ambient > 0) {
    channels.forEach((channel, channelIndex) => {
      const noise = makeNoiseWave(sampleRate, duration, 7001 + channelIndex * 977);
      for (let index = 0; index < channel.length; index += 1) {
        channel[index] += noise[index] * ambient;
      }
    });
  }

  let peak = 0;
  channels.forEach((channel) => {
    for (let index = 0; index < channel.length; index += 1) {
      peak = Math.max(peak, Math.abs(channel[index]));
    }
  });
  const scale = peak > 0.98 ? 0.92 / peak : 1;
  channels.forEach((channel) => {
    for (let index = 0; index < channel.length; index += 1) channel[index] *= scale;
  });

  return {
    channels,
    sampleRate,
    duration,
    sources: sources.map((source) => ({
      ...source,
      angleDeg: normalizeDegrees(source.angleDeg)
    }))
  };
}

export function swapChannels(channels, first, second) {
  const copy = channels.slice();
  [copy[first], copy[second]] = [copy[second], copy[first]];
  return copy;
}

export function mixDown(channels) {
  const length = channels[0]?.length ?? 0;
  const output = new Float32Array(length);
  channels.forEach((channel) => {
    for (let index = 0; index < length; index += 1) output[index] += channel[index] / channels.length;
  });
  return output;
}

function meanEnergy(signals) {
  let total = 0;
  signals.forEach((signal) => {
    for (let index = 0; index < signal.length; index += 1) total += signal[index] * signal[index];
  });
  return total / Math.max(1, signals.length * (signals[0]?.length ?? 1));
}

export function scanBeam({
  channels,
  array,
  speedOfSound = 343,
  sampleRate = 16000,
  stepDeg = 2
}) {
  const count = Math.max(1, Math.round(360 / stepDeg));
  const actualStep = 360 / count;
  const angles = Array.from({ length: count }, (_, index) => index * actualStep);
  const power = new Float64Array(count);
  const coherence = new Float64Array(count);
  const channelCount = channels.length;
  const micEnergy = meanEnergy(channels);
  const maxSoundDelay = Math.max(...array.map((mic) => Math.hypot(mic.x, mic.y))) / speedOfSound;
  const guard = Math.ceil(maxSoundDelay * sampleRate) + 2;
  const length = channels[0]?.length ?? 0;
  const usableLength = Math.max(1, length - 2 * guard);

  for (let candidateIndex = 0; candidateIndex < count; candidateIndex += 1) {
    const phi = angles[candidateIndex] * DEG;
    const cosPhi = Math.cos(phi);
    const sinPhi = Math.sin(phi);
    const delays = array.map((mic) => (
      (mic.x * cosPhi + mic.y * sinPhi) / speedOfSound * sampleRate
    ));
    let energy = 0;

    for (let sample = guard; sample < length - guard; sample += 1) {
      let sum = 0;
      for (let channelIndex = 0; channelIndex < channelCount; channelIndex += 1) {
        const signal = channels[channelIndex];
        const position = sample + delays[channelIndex];
        const index = position | 0;
        const fraction = position - index;
        if (index >= 0 && index < length - 1) {
          sum += signal[index] + fraction * (signal[index + 1] - signal[index]);
        }
      }
      const value = sum / channelCount;
      energy += value * value;
    }

    power[candidateIndex] = energy / usableLength;
    const floor = 1 / Math.max(2, channelCount);
    coherence[candidateIndex] = clamp(
      (power[candidateIndex] / Math.max(1e-12, micEnergy) - floor) / (1 - floor),
      0,
      1
    );
  }

  const maxPower = Math.max(...power);
  const db = Array.from(power, (value) => 10 * Math.log10(Math.max(1e-12, value) / Math.max(1e-12, maxPower)));
  return {
    angles,
    stepDeg: actualStep,
    power,
    db,
    coherence,
    peakPower: maxPower,
    channelEnergy: micEnergy
  };
}

function smoothCircular(values, kernel = [1, 2, 3, 2, 1]) {
  const radius = Math.floor(kernel.length / 2);
  const output = new Float64Array(values.length);
  let sumWeights = 0;
  kernel.forEach((weight) => { sumWeights += weight; });
  for (let index = 0; index < values.length; index += 1) {
    let sum = 0;
    for (let tap = -radius; tap <= radius; tap += 1) {
      const wrapped = (index + tap + values.length) % values.length;
      sum += values[wrapped] * kernel[tap + radius];
    }
    output[index] = sum / sumWeights;
  }
  return output;
}

function refinePeak(angles, power, index) {
  const count = angles.length;
  const left = power[(index - 1 + count) % count];
  const center = power[index];
  const right = power[(index + 1) % count];
  const denominator = left - 2 * center + right;
  if (Math.abs(denominator) < 1e-14) return angles[index];
  const offset = clamp(0.5 * (left - right) / denominator, -0.5, 0.5);
  return normalizeDegrees(angles[index] + offset * (angles.length > 1 ? angles[1] - angles[0] : 1));
}

export function findPeaks(scan, { maxPeaks = 16, minSeparationDeg = 8 } = {}) {
  const count = scan.angles.length;
  const smoothed = smoothCircular(scan.power);
  const maxPower = Math.max(...smoothed);
  const minimumIndexSpacing = Math.max(1, Math.round(minSeparationDeg / scan.stepDeg));
  const candidates = [];

  for (let index = 0; index < count; index += 1) {
    const previous = smoothed[(index - 1 + count) % count];
    const next = smoothed[(index + 1) % count];
    if (smoothed[index] >= previous && smoothed[index] > next) {
      candidates.push({
        index,
        angle: refinePeak(scan.angles, scan.power, index),
        power: scan.power[index],
        coherence: scan.coherence[index]
      });
    }
  }

  candidates.sort((a, b) => b.power - a.power);
  const selected = [];
  candidates.forEach((candidate) => {
    if (selected.length >= maxPeaks) return;
    if (selected.some((peak) => angularDistance(peak.angle, candidate.angle) < minSeparationDeg)) return;
    selected.push(candidate);
  });

  const median = [...scan.power].sort((a, b) => a - b)[Math.floor(count / 2)] || 1e-12;
  return selected.map((peak, rank) => ({
    ...peak,
    rank,
    relativeDb: 10 * Math.log10(Math.max(1e-12, peak.power) / Math.max(1e-12, maxPower)),
    contrastDb: 10 * Math.log10(Math.max(1e-12, peak.power) / median),
    confidence: clamp(peak.coherence, 0, 1)
  }));
}

export function isUniformRing(array) {
  if (array.length < 3) return false;
  const radii = array.map((mic) => Math.hypot(mic.x, mic.y));
  const meanRadius = radii.reduce((sum, radius) => sum + radius, 0) / radii.length;
  const radiiMatch = radii.every((radius) => Math.abs(radius - meanRadius) < meanRadius * 0.012);
  const sortedAngles = array.map((mic) => Math.atan2(mic.y, mic.x) * 180 / Math.PI).sort((a, b) => a - b);
  const start = sortedAngles[0];
  const expected = 360 / array.length;
  const spacingMatch = sortedAngles.every((angle, index) => (
    Math.abs(angularDistance(angle - start, index * expected)) < 2
  ));
  return radiiMatch && spacingMatch && array.length > 2;
}

export function symmetryCandidates(angleDeg, symmetryDeg) {
  if (!symmetryDeg) return [normalizeDegrees(angleDeg)];
  const result = [];
  for (let angle = 0; angle < 360; angle += symmetryDeg) {
    result.push(normalizeDegrees(Math.round((angle + angleDeg % symmetryDeg) * 10) / 10));
  }
  return [...new Set(result)].sort((a, b) => a - b);
}

export function foldedAngle(angleDeg, symmetryDeg) {
  if (!symmetryDeg) return normalizeDegrees(angleDeg);
  return normalizeDegrees(angleDeg) % symmetryDeg;
}

function estimatePairLag(first, second, maxLag) {
  let bestLag = 0;
  let bestCorrelation = -Infinity;
  const length = Math.min(first.length, second.length);
  const startOffset = Math.min(Math.floor(length * 0.08), 1200);
  const endOffset = Math.min(Math.floor(length * 0.82), length - maxLag - 1);

  for (let lag = -maxLag; lag <= maxLag; lag += 1) {
    let dot = 0;
    let energyFirst = 0;
    let energySecond = 0;
    const start = Math.max(startOffset, startOffset - lag);
    const end = Math.min(endOffset, endOffset - lag);
    for (let index = start; index < end; index += 2) {
      const a = first[index];
      const b = second[index + lag];
      dot += a * b;
      energyFirst += a * a;
      energySecond += b * b;
    }
    const correlation = dot / Math.sqrt(Math.max(1e-12, energyFirst * energySecond));
    if (correlation > bestCorrelation) {
      bestCorrelation = correlation;
      bestLag = lag;
    }
  }
  return { lag: bestLag, correlation: Math.max(0, bestCorrelation) };
}

export function diagnoseChannelMap({
  channels,
  array,
  speedOfSound = 343,
  sampleRate = 16000,
  referenceAngle = 0,
  candidateAngles = []
}) {
  const maxRadius = Math.max(...array.map((mic) => Math.hypot(mic.x, mic.y)));
  const maxLag = Math.max(2, Math.ceil(2 * maxRadius / speedOfSound * sampleRate) + 1);
  const observed = [];

  for (let first = 0; first < channels.length; first += 1) {
    for (let second = first + 1; second < channels.length; second += 1) {
      const estimate = estimatePairLag(channels[first], channels[second], maxLag);
      observed.push({ first, second, ...estimate });
    }
  }

  const mappings = [{ name: 'identity', perm: array.map((_, index) => index), swap: null }];
  for (let first = 0; first < array.length; first += 1) {
    for (let second = first + 1; second < array.length; second += 1) {
      const perm = array.map((_, index) => index);
      [perm[first], perm[second]] = [perm[second], perm[first]];
      mappings.push({ name: `swap-${first + 1}-${second + 1}`, perm, swap: [first, second] });
    }
  }

  const angleHypotheses = candidateAngles.length
    ? [...new Set(candidateAngles.map((angle) => normalizeDegrees(angle)))]
    : [normalizeDegrees(referenceAngle)];
  const scored = mappings.map((mapping) => {
    let error = 0;
    let weightSum = 0;
    observed.forEach(({ first, second, lag, correlation }) => {
      const firstMic = array[mapping.perm[first]];
      const secondMic = array[mapping.perm[second]];
      let pairError = Infinity;
      angleHypotheses.forEach((hypothesis) => {
        const phi = hypothesis * DEG;
        const projection = (secondMic.x - firstMic.x) * Math.cos(phi)
          + (secondMic.y - firstMic.y) * Math.sin(phi);
        const predicted = projection / speedOfSound * sampleRate;
        pairError = Math.min(pairError, (lag - predicted) ** 2);
      });
      const weight = Math.max(0.04, correlation * correlation);
      error += weight * pairError;
      weightSum += weight;
    });
    return {
      ...mapping,
      residual: Math.sqrt(error / Math.max(1e-9, weightSum))
    };
  });

  scored.sort((a, b) => a.residual - b.residual);
  const best = scored[0];
  const identity = scored.find((mapping) => mapping.name === 'identity');
  const improvement = identity.residual > 1e-9
    ? 1 - best.residual / identity.residual
    : 0;
  const stronglyImproved = improvement > 0.1 && identity.residual > 0.34;
  const weaklyImproved = improvement > 0.015 && identity.residual > 0.7;
  const suspectSwap = Boolean(best.swap) && (stronglyImproved || weaklyImproved);

  return {
    consistent: !suspectSwap,
    suspectSwap: suspectSwap ? best.swap : null,
    recommendedMapping: suspectSwap ? best.name : 'identity',
    bestResidual: best.residual,
    identityResidual: identity.residual,
    residualInSamples: best.residual,
    improvement,
    bestAngle: normalizeDegrees(referenceAngle),
    pairCount: observed.length,
    observed,
    confidence: clamp(improvement * 1.4, 0, 1)
  };
}

function normalizeAudio(samples, peakTarget = 0.9) {
  let peak = 0;
  for (let index = 0; index < samples.length; index += 1) {
    peak = Math.max(peak, Math.abs(samples[index]));
  }
  if (peak < 1e-9) return samples;
  const scale = Math.min(2.5, peakTarget / peak);
  for (let index = 0; index < samples.length; index += 1) samples[index] *= scale;
  return samples;
}

export function beamformAtAngle({
  channels,
  array,
  angleDeg,
  speedOfSound = 343,
  sampleRate = 16000
}) {
  const maxRadius = Math.max(...array.map((mic) => Math.hypot(mic.x, mic.y)));
  const guard = Math.ceil(maxRadius / speedOfSound * sampleRate) + 2;
  const length = channels[0]?.length ?? 0;
  const output = new Float32Array(Math.max(1, length - 2 * guard));
  const phi = angleDeg * DEG;
  const delays = array.map((mic) => (
    (mic.x * Math.cos(phi) + mic.y * Math.sin(phi)) / speedOfSound * sampleRate
  ));

  for (let index = 0; index < output.length; index += 1) {
    const sample = index + guard;
    let sum = 0;
    for (let channelIndex = 0; channelIndex < channels.length; channelIndex += 1) {
      const position = sample + delays[channelIndex];
      const left = position | 0;
      const fraction = position - left;
      if (left >= 0 && left < length - 1) {
        sum += channels[channelIndex][left]
          + fraction * (channels[channelIndex][left + 1] - channels[channelIndex][left]);
      }
    }
    output[index] = sum / channels.length;
  }

  const fadeSamples = Math.min(Math.floor(sampleRate * 0.012), Math.floor(output.length / 4));
  for (let index = 0; index < fadeSamples; index += 1) {
    const fade = index / fadeSamples;
    output[index] *= fade;
    output[output.length - 1 - index] *= fade;
  }
  return normalizeAudio(output);
}

export function audioRms(samples) {
  let energy = 0;
  for (let index = 0; index < samples.length; index += 1) energy += samples[index] * samples[index];
  return Math.sqrt(energy / Math.max(1, samples.length));
}

export function encodeWav(samples, sampleRate) {
  const bytesPerSample = 2;
  const dataSize = samples.length * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);
  const writeString = (offset, text) => {
    for (let index = 0; index < text.length; index += 1) {
      view.setUint8(offset + index, text.charCodeAt(index));
    }
  };

  writeString(0, 'RIFF');
  view.setUint32(4, 36 + dataSize, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * bytesPerSample, true);
  view.setUint16(32, bytesPerSample, true);
  view.setUint16(34, 16, true);
  writeString(36, 'data');
  view.setUint32(40, dataSize, true);

  for (let index = 0; index < samples.length; index += 1) {
    const value = clamp(samples[index], -1, 1);
    view.setInt16(44 + index * 2, value < 0 ? value * 0x8000 : value * 0x7fff, true);
  }
  return buffer;
}
