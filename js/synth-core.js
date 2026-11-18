(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.SynthCore = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const TWO_PI = Math.PI * 2;
  const A4 = 440;
  const A4_MIDI = 69;

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function midiToFrequency(midi) {
    return A4 * Math.pow(2, (midi - A4_MIDI) / 12);
  }

  function frequencyToMidi(frequency) {
    return A4_MIDI + 12 * Math.log2(frequency / A4);
  }

  function centsBetween(measured, target) {
    return 1200 * Math.log2(measured / target);
  }

  const NOTE_NAMES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"];

  function midiToNoteName(midi) {
    const value = Math.round(midi);
    const name = NOTE_NAMES[((value % 12) + 12) % 12];
    const octave = Math.floor(value / 12) - 1;
    return name + octave;
  }

  function mulberry32(seed) {
    let state = seed >>> 0;
    return function random() {
      state += 0x6d2b79f5;
      let value = state;
      value = Math.imul(value ^ (value >>> 15), value | 1);
      value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
      return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
    };
  }

  function fft(real, imag) {
    const size = real.length;
    for (let i = 1, j = 0; i < size; i += 1) {
      let bit = size >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) {
        const realTemp = real[i];
        const imagTemp = imag[i];
        real[i] = real[j];
        imag[i] = imag[j];
        real[j] = realTemp;
        imag[j] = imagTemp;
      }
    }

    for (let length = 2; length <= size; length <<= 1) {
      const angle = -TWO_PI / length;
      const stepReal = Math.cos(angle);
      const stepImag = Math.sin(angle);
      for (let offset = 0; offset < size; offset += length) {
        let twiddleReal = 1;
        let twiddleImag = 0;
        for (let index = 0; index < length / 2; index += 1) {
          const even = offset + index;
          const odd = even + length / 2;
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
  }

  function nextPowerOfTwo(value) {
    let result = 1;
    while (result < value) result <<= 1;
    return result;
  }

  function loopParameters(frequency, sampleRate, damping, hardness) {
    const safeFrequency = clamp(frequency, 20, sampleRate * 0.35);
    const safeDamping = clamp(damping, 0, 1);
    const safeHardness = clamp(hardness, 0, 1);
    const t60 = 0.34 + 6.6 * Math.pow(1 - safeDamping, 2.25);
    const cutoff = clamp(
      (620 + Math.pow(safeHardness, 1.75) * 17800) * (1 - safeDamping * 0.62) + 280,
      420,
      sampleRate * 0.44
    );
    const lowpassCoefficient = Math.exp((-TWO_PI * cutoff) / sampleRate);
    const omega = (TWO_PI * safeFrequency) / sampleRate;
    const filterPhase = Math.atan2(
      lowpassCoefficient * Math.sin(omega),
      1 - lowpassCoefficient * Math.cos(omega)
    ) / omega;
    const desiredDelay = sampleRate / safeFrequency - filterPhase;
    const integerDelay = Math.max(2, Math.floor(desiredDelay));
    const fractionalDelay = clamp(desiredDelay - integerDelay, 0.08, 0.92);
    const allpassCoefficient = (1 - fractionalDelay) / (1 + fractionalDelay);
    const loopGain = Math.min(0.99994, Math.pow(10, -3 / (safeFrequency * t60)));

    return {
      frequency: safeFrequency,
      damping: safeDamping,
      hardness: safeHardness,
      t60,
      cutoff,
      lowpassCoefficient,
      filterPhase,
      desiredDelay,
      integerDelay,
      fractionalDelay,
      allpassCoefficient,
      loopGain
    };
  }

  function buildExcitation(length, position, hardness, seed) {
    const beta = clamp(position, 0.035, 0.49);
    const random = mulberry32(seed);
    const raw = new Float64Array(length);
    let mean = 0;

    for (let index = 0; index < length; index += 1) {
      const x = index / Math.max(1, length - 1);
      const triangular = x < beta ? x / beta : (1 - x) / (1 - beta);
      const softNoise = (random() * 2 - 1) * 0.018 * (1 - hardness * 0.55);
      raw[index] = triangular + softNoise;
      mean += raw[index];
    }

    mean /= Math.max(1, length);
    let maximum = 1e-12;
    for (let index = 0; index < length; index += 1) {
      raw[index] -= mean;
      maximum = Math.max(maximum, Math.abs(raw[index]));
    }
    for (let index = 0; index < length; index += 1) raw[index] /= maximum;
    return raw;
  }

  function makeEnvelope(samples, sampleRate, hopSize) {
    const frameCount = Math.max(1, Math.ceil(samples.length / hopSize));
    const envelope = new Float64Array(frameCount);
    for (let frame = 0; frame < frameCount; frame += 1) {
      const start = frame * hopSize;
      const end = Math.min(samples.length, start + hopSize);
      let sum = 0;
      for (let index = start; index < end; index += 1) sum += samples[index] * samples[index];
      envelope[frame] = Math.sqrt(sum / Math.max(1, end - start));
    }
    return envelope;
  }

  function estimateT60(envelope, hopSize, sampleRate) {
    let peakDb = -Infinity;
    for (let index = 0; index < envelope.length; index += 1) {
      if (envelope[index] > 0) peakDb = Math.max(peakDb, 20 * Math.log10(envelope[index]));
    }
    if (!Number.isFinite(peakDb)) return 0;

    let sumX = 0;
    let sumY = 0;
    let sumXX = 0;
    let sumXY = 0;
    let count = 0;
    for (let index = 0; index < envelope.length; index += 1) {
      if (envelope[index] <= 0) continue;
      const db = 20 * Math.log10(envelope[index]) - peakDb;
      if (db > -4 || db < -46) continue;
      const x = (index * hopSize) / sampleRate;
      sumX += x;
      sumY += db;
      sumXX += x * x;
      sumXY += x * db;
      count += 1;
    }
    if (count < 6) return 0;
    const denominator = count * sumXX - sumX * sumX;
    if (Math.abs(denominator) < 1e-12) return 0;
    const slope = (count * sumXY - sumX * sumY) / denominator;
    return slope < -0.1 ? clamp(-60 / slope, 0.01, 30) : 0;
  }

  function renderNote(options) {
    const settings = options || {};
    const sampleRate = settings.sampleRate || 48000;
    const frequency = settings.frequency || 220;
    const damping = settings.damping == null ? 0.46 : settings.damping;
    const hardness = settings.hardness == null ? 0.58 : settings.hardness;
    const position = settings.position == null ? 0.21 : settings.position;
    const velocity = settings.velocity == null ? 0.82 : settings.velocity;
    const seed = settings.seed == null ? 0x51a7 : settings.seed;
    const loop = loopParameters(frequency, sampleRate, damping, hardness);
    const requestedDuration = settings.duration == null
      ? clamp(loop.t60 * 1.55 + 0.25, 0.75, 12)
      : clamp(settings.duration, 0.25, 30);
    const totalSamples = Math.max(64, Math.ceil(requestedDuration * sampleRate));
    const output = new Float32Array(totalSamples);
    const queueLength = loop.integerDelay + 1;
    const delayLine = new Float64Array(queueLength);
    const excitation = buildExcitation(
      loop.integerDelay,
      position,
      loop.hardness,
      seed
    );

    for (let index = 0; index < loop.integerDelay; index += 1) {
      delayLine[index] = excitation[index] * 0.72;
    }

    let readIndex = 0;
    let writeIndex = loop.integerDelay;
    let lowpassState = 0;
    let allpassInputState = 0;
    let allpassOutputState = 0;
    let peak = 1e-12;
    const fadeSamples = Math.min(1280, Math.floor(totalSamples * 0.04));

    for (let sample = 0; sample < totalSamples; sample += 1) {
      const delayed = delayLine[readIndex];
      const lowpassed = (1 - loop.lowpassCoefficient) * delayed
        + loop.lowpassCoefficient * lowpassState;
      lowpassState = lowpassed;

      const allpassed = loop.allpassCoefficient * lowpassed
        + allpassInputState
        - loop.allpassCoefficient * allpassOutputState;
      allpassInputState = lowpassed;
      allpassOutputState = allpassed;

      const feedback = allpassed * loop.loopGain;
      delayLine[writeIndex] = Number.isFinite(feedback) ? feedback : 0;
      output[sample] = delayed;
      peak = Math.max(peak, Math.abs(delayed));

      readIndex = (readIndex + 1) % queueLength;
      writeIndex = (writeIndex + 1) % queueLength;
    }

    const scale = (0.86 * clamp(velocity, 0, 1)) / peak;
    for (let sample = 0; sample < totalSamples; sample += 1) {
      let value = output[sample] * scale;
      if (sample >= totalSamples - fadeSamples) {
        const phase = (sample - (totalSamples - fadeSamples)) / fadeSamples;
        value *= 0.5 + 0.5 * Math.cos(Math.PI * phase);
      }
      output[sample] = clamp(value, -1, 1);
    }

    const hopSize = Math.max(64, Math.round(sampleRate / 200));
    const envelope = makeEnvelope(output, sampleRate, hopSize);
    const measuredT60 = estimateT60(envelope, hopSize, sampleRate);
    let peakSample = 0;
    let residualPeak = 0;
    for (let index = 0; index < totalSamples; index += 1) {
      peakSample = Math.max(peakSample, Math.abs(output[index]));
      if (index >= totalSamples - 256) residualPeak = Math.max(residualPeak, Math.abs(output[index]));
    }

    return {
      samples: output,
      sampleRate,
      duration: totalSamples / sampleRate,
      frequency,
      parameters: {
        frequency,
        damping,
        hardness,
        position,
        velocity,
        seed
      },
      loop,
      stats: {
        peak: peakSample,
        t60: measuredT60,
        targetT60: loop.t60,
        residualPeak,
        samples: totalSamples
      },
      envelope: {
        data: envelope,
        hopSize,
        sampleRate
      }
    };
  }

  function hanning(index, length) {
    return 0.5 - 0.5 * Math.cos((TWO_PI * index) / Math.max(1, length - 1));
  }

  function spectralPitchTrack(samples, sampleRate, targetFrequency, options) {
    const settings = options || {};
    const startSeconds = settings.startSeconds == null ? 0.12 : settings.startSeconds;
    const requestedSize = settings.fftSize || 65536;
    const fftSize = nextPowerOfTwo(Math.max(8192, requestedSize));
    const start = Math.min(samples.length - 64, Math.floor(startSeconds * sampleRate));
    const available = Math.max(64, samples.length - Math.max(0, start));
    const windowLength = Math.min(available, Math.floor(fftSize * 0.9));
    const real = new Float64Array(fftSize);
    const imag = new Float64Array(fftSize);
    let windowSum = 0;

    for (let index = 0; index < windowLength; index += 1) {
      const window = hanning(index, windowLength);
      real[index] = samples[Math.max(0, start) + index] * window;
      windowSum += window;
    }
    fft(real, imag);

    const half = fftSize / 2;
    const binHz = sampleRate / fftSize;
    const magnitudes = new Float64Array(half);
    for (let index = 1; index < half; index += 1) {
      magnitudes[index] = (2 * Math.hypot(real[index], imag[index])) / Math.max(1, windowSum);
    }

    const peaks = [];
    const maxHarmonic = Math.min(12, Math.floor((sampleRate * 0.45) / targetFrequency));
    let strongest = 1e-12;
    for (let harmonic = 1; harmonic <= maxHarmonic; harmonic += 1) {
      const expected = targetFrequency * harmonic;
      const searchBins = Math.max(3, Math.round(expected * 0.035 / binHz));
      const centerBin = Math.round(expected / binHz);
      const firstBin = Math.max(2, centerBin - searchBins);
      const lastBin = Math.min(half - 2, centerBin + searchBins);
      let peakBin = firstBin;
      for (let bin = firstBin + 1; bin <= lastBin; bin += 1) {
        if (magnitudes[bin] > magnitudes[peakBin]) peakBin = bin;
      }
      const left = magnitudes[peakBin - 1];
      const center = magnitudes[peakBin];
      const right = magnitudes[peakBin + 1];
      if (center < 1e-9) continue;
      const denominator = left - 2 * center + right;
      let offset = denominator !== 0 ? (0.5 * (left - right)) / denominator : 0;
      offset = clamp(offset, -0.5, 0.5);
      const measured = (peakBin + offset) * binHz;
      strongest = Math.max(strongest, center);
      peaks.push({
        harmonic,
        frequency: measured,
        amplitude: center,
        expectedFrequency: expected
      });
    }

    const validPeaks = peaks.filter((peak) => peak.amplitude > strongest * 0.008);
    let weightedFrequency = 0;
    let weightSum = 0;
    for (const peak of validPeaks) {
      const weight = peak.amplitude * Math.sqrt(peak.harmonic);
      weightedFrequency += (peak.frequency / peak.harmonic) * weight;
      weightSum += weight;
    }
    const measuredFrequency = weightSum > 0
      ? weightedFrequency / weightSum
      : targetFrequency;

    return {
      targetFrequency,
      measuredFrequency,
      cents: centsBetween(measuredFrequency, targetFrequency),
      peaks: validPeaks,
      fftSize,
      windowLength,
      binHz
    };
  }

  function harmonicAnalysis(samples, sampleRate, fundamental, count, options) {
    const settings = options || {};
    const harmonicCount = clamp(Math.round(count || 12), 1, 32);
    const start = Math.floor((settings.startSeconds == null ? 0.03 : settings.startSeconds) * sampleRate);
    const available = Math.max(32, samples.length - start);
    const maxLength = settings.windowSeconds == null ? 0.35 : settings.windowSeconds;
    const length = Math.min(available, Math.floor(maxLength * sampleRate));
    let windowSum = 0;
    const output = [];

    for (let harmonic = 1; harmonic <= harmonicCount; harmonic += 1) {
      const harmonicFrequency = fundamental * harmonic;
      if (harmonicFrequency >= sampleRate * 0.47) break;
      const omega = (TWO_PI * harmonicFrequency) / sampleRate;
      let real = 0;
      let imag = 0;
      for (let index = 0; index < length; index += 1) {
        const window = hanning(index, length);
        const value = samples[start + index] * window;
        real += value * Math.cos(omega * index);
        imag -= value * Math.sin(omega * index);
        if (harmonic === 1) windowSum += window;
      }
      output.push({
        harmonic,
        frequency: harmonicFrequency,
        amplitude: (2 * Math.hypot(real, imag)) / Math.max(1, windowSum)
      });
    }
    return output;
  }

  function comparePluckPoints(options) {
    const settings = options || {};
    const sampleRate = settings.sampleRate || 48000;
    const frequency = settings.frequency || 220;
    const harmonicCount = settings.harmonicCount || 12;
    const common = {
      sampleRate,
      frequency,
      damping: settings.damping == null ? 0.46 : settings.damping,
      hardness: settings.hardness == null ? 0.58 : settings.hardness,
      velocity: settings.velocity == null ? 0.8 : settings.velocity,
      duration: settings.duration || 1.2
    };
    const renderA = renderNote(Object.assign({}, common, {
      position: settings.positionA == null ? 0.2 : settings.positionA,
      seed: 0x2a11
    }));
    const renderB = renderNote(Object.assign({}, common, {
      position: settings.positionB == null ? 0.33 : settings.positionB,
      seed: 0x2a11
    }));
    const harmonicsA = harmonicAnalysis(renderA.samples, sampleRate, frequency, harmonicCount);
    const harmonicsB = harmonicAnalysis(renderB.samples, sampleRate, frequency, harmonicCount);
    const maximum = Math.max(
      1e-12,
      ...harmonicsA.map((item) => item.amplitude),
      ...harmonicsB.map((item) => item.amplitude)
    );
    const toDb = (amplitude) => 20 * Math.log10(Math.max(1e-8, amplitude / maximum));
    const compared = harmonicsA.map((item, index) => ({
      harmonic: item.harmonic,
      frequency: item.frequency,
      amplitudeA: item.amplitude,
      amplitudeB: harmonicsB[index] ? harmonicsB[index].amplitude : 0,
      dbA: toDb(item.amplitude),
      dbB: toDb(harmonicsB[index] ? harmonicsB[index].amplitude : 0)
    }));

    let deepest = null;
    for (const harmonic of compared) {
      const difference = Math.abs(harmonic.dbA - harmonic.dbB);
      if (!deepest || difference > deepest.difference) {
        deepest = Object.assign({ difference }, harmonic);
      }
    }

    return {
      positionA: settings.positionA == null ? 0.2 : settings.positionA,
      positionB: settings.positionB == null ? 0.33 : settings.positionB,
      frequency,
      harmonics: compared,
      deepest,
      renderA,
      renderB
    };
  }

  function renderTimeline(events, options) {
    const settings = options || {};
    const sampleRate = settings.sampleRate || 48000;
    const plucks = (events || [])
      .filter((event) => event.type === "pluck" || event.midi != null || event.frequency != null)
      .slice()
      .sort((left, right) => left.time - right.time);
    if (!plucks.length) {
      return {
        samples: new Float32Array(0),
        sampleRate,
        duration: 0,
        notes: []
      };
    }

    const rendered = plucks.map((event, index) => {
      const frequency = event.frequency || midiToFrequency(event.midi);
      const note = renderNote({
        sampleRate,
        frequency,
        position: event.position == null ? 0.21 : event.position,
        damping: event.damping == null ? 0.46 : event.damping,
        hardness: event.hardness == null ? 0.58 : event.hardness,
        velocity: event.velocity == null ? 0.82 : event.velocity,
        seed: event.seed == null ? 0x600d + index * 71 : event.seed
      });
      return { event, note, startSample: Math.max(0, Math.round(event.time * sampleRate)) };
    });

    const maxEnd = Math.max(...rendered.map((item) => item.startSample + item.note.samples.length));
    const samples = new Float32Array(maxEnd);
    for (const item of rendered) {
      for (let index = 0; index < item.note.samples.length; index += 1) {
        samples[item.startSample + index] += item.note.samples[index] * 0.72;
      }
    }
    let peak = 1e-12;
    for (let index = 0; index < samples.length; index += 1) peak = Math.max(peak, Math.abs(samples[index]));
    if (peak > 0.98) {
      const scale = 0.98 / peak;
      for (let index = 0; index < samples.length; index += 1) samples[index] *= scale;
    }
    return {
      samples,
      sampleRate,
      duration: samples.length / sampleRate,
      notes: rendered
    };
  }

  function encodeWav(samples, sampleRate, options) {
    const settings = options || {};
    const channels = settings.channels || 1;
    const bytesPerSample = 2;
    const dataLength = samples.length * bytesPerSample;
    const buffer = new ArrayBuffer(44 + dataLength);
    const view = new DataView(buffer);
    const writeString = (offset, text) => {
      for (let index = 0; index < text.length; index += 1) {
        view.setUint8(offset + index, text.charCodeAt(index));
      }
    };

    writeString(0, "RIFF");
    view.setUint32(4, 36 + dataLength, true);
    writeString(8, "WAVE");
    writeString(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, channels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * channels * bytesPerSample, true);
    view.setUint16(32, channels * bytesPerSample, true);
    view.setUint16(34, 16, true);
    writeString(36, "data");
    view.setUint32(40, dataLength, true);

    for (let index = 0; index < samples.length; index += 1) {
      const sample = clamp(samples[index], -1, 1);
      const value = sample < 0 ? Math.round(sample * 32768) : Math.round(sample * 32767);
      view.setInt16(44 + index * 2, value, true);
    }
    return new Uint8Array(buffer);
  }

  return {
    A4,
    clamp,
    midiToFrequency,
    frequencyToMidi,
    midiToNoteName,
    centsBetween,
    loopParameters,
    buildExcitation,
    renderNote,
    spectralPitchTrack,
    harmonicAnalysis,
    comparePluckPoints,
    renderTimeline,
    encodeWav
  };
});
