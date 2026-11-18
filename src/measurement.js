import { DelayEngine } from "./dsp.js";

const SAMPLE_RATE = 48000;
const TEST_DELAY_MS = 23.4;
const JUMP_CROSSFADE_MS = 18;

function yieldToBrowser() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function toDb(value) {
  return value > 1e-9 ? 20 * Math.log10(value) : -120;
}

function makeEngine(mode) {
  const engine = new DelayEngine({
    sampleRate: SAMPLE_RATE,
    feedback: 0,
    mix: 1,
    interpolation: mode,
    crossfadeMs: JUMP_CROSSFADE_MS,
  });
  engine.setDelayMs(TEST_DELAY_MS, { immediate: true });
  return engine;
}

function measureImpulse(mode) {
  const engine = makeEngine(mode);
  const delaySamples = engine.effectiveDelaySamples;
  const impulseAt = 64;
  const total = Math.ceil(delaySamples) + impulseAt + 64;
  const input = new Float32Array(total);
  const output = new Float32Array(total);
  input[impulseAt] = 1;
  engine.processBlock(input, output);

  const center = impulseAt + delaySamples;
  const start = Math.max(1, Math.floor(center) - 18);
  const end = Math.min(total, Math.ceil(center) + 19);
  let weight = 0;
  let moment = 0;
  let energy = 0;
  let peak = 0;
  for (let index = start; index < end; index += 1) {
    const absolute = Math.abs(output[index]);
    const power = output[index] * output[index];
    weight += absolute;
    moment += index * absolute;
    energy += power;
    peak = Math.max(peak, absolute);
  }
  const centroid = weight > 1e-12 ? moment / weight : 0;
  const measuredDelay = centroid - impulseAt;

  return {
    trace: {
      offsets: Array.from({ length: end - start }, (_, i) => ((start + i - center) / SAMPLE_RATE) * 1000),
      values: Array.from(output.slice(start, end)),
    },
    metrics: {
      targetMs: (delaySamples / SAMPLE_RATE) * 1000,
      measuredMs: (measuredDelay / SAMPLE_RATE) * 1000,
      errorSamples: measuredDelay - delaySamples,
      fraction: delaySamples % 1,
      peak,
      energy,
    },
  };
}

function goertzelMagnitude(samples, start, length, frequency) {
  const coefficient = 2 * Math.cos((2 * Math.PI * frequency) / SAMPLE_RATE);
  let previous = 0;
  let previousPrevious = 0;
  for (let index = 0; index < length; index += 1) {
    const window = 0.5 - 0.5 * Math.cos((2 * Math.PI * index) / (length - 1));
    const current = samples[start + index] * window + coefficient * previous - previousPrevious;
    previousPrevious = previous;
    previous = current;
  }
  const power =
    previous * previous +
    previousPrevious * previousPrevious -
    coefficient * previous * previousPrevious;
  return Math.sqrt(Math.max(0, power));
}

function generateSweep(duration = 2.6, f0 = 35, f1 = 18000) {
  const length = Math.round(duration * SAMPLE_RATE);
  const samples = new Float32Array(length);
  const sweepEnd = 1.9;
  let phase = 0;
  for (let index = 0; index < length; index += 1) {
    const time = index / SAMPLE_RATE;
    const progress = Math.min(1, time / sweepEnd);
    const frequency = f0 * Math.pow(f1 / f0, progress);
    phase += (2 * Math.PI * frequency) / SAMPLE_RATE;
    const fade = Math.min(1, time / 0.03, (duration - time) / 0.03);
    samples[index] = Math.sin(phase) * 0.24 * Math.max(0, fade);
  }
  return samples;
}

function measureSweep(mode) {
  const engine = makeEngine(mode);
  engine.setDelayMs(TEST_DELAY_MS, { immediate: true });
  const duration = 2.6;
  const input = generateSweep(duration);
  const output = new Float32Array(input.length);
  engine.processBlock(input, output);

  const frameSize = 4096;
  const hop = 1024;
  const sweepEnd = 1.9;
  const delaySeconds = engine.effectiveDelaySamples / SAMPLE_RATE;
  const sweepFrequency = (time) =>
    35 * Math.pow(18000 / 35, Math.min(1, Math.max(0, time / sweepEnd)));
  const points = [];
  for (let start = 0; start + frameSize < input.length; start += hop) {
    const centerTime = (start + frameSize / 2) / SAMPLE_RATE;
    if (centerTime < 0.12 || centerTime > duration - 0.12) continue;
    if (centerTime > sweepEnd - 0.1 && centerTime < sweepEnd + delaySeconds + 0.12) continue;
    const frequency = sweepFrequency(centerTime);
    if (frequency < 40 || frequency > 18000.5) continue;
    const delayedFrequency = sweepFrequency(centerTime - delaySeconds);
    const inputMagnitude = goertzelMagnitude(input, start, frameSize, frequency);
    const outputMagnitude = goertzelMagnitude(output, start, frameSize, delayedFrequency);
    if (inputMagnitude < 1e-8) continue;
    points.push({
      frequency,
      db: toDb(outputMagnitude / inputMagnitude),
    });
  }

  const deviations = points.map((point) => Math.abs(point.db));
  const average = deviations.reduce((sum, value) => sum + value, 0) / Math.max(1, deviations.length);
  const maximum = Math.max(...points.map((point) => Math.abs(point.db)));
  const nearest = (frequency) => {
    const closest = [...points]
      .sort(
        (left, right) =>
          Math.abs(left.frequency - frequency) - Math.abs(right.frequency - frequency),
      )
      .slice(0, 3)
      .map((point) => point.db)
      .sort((left, right) => left - right);
    return closest[Math.floor(closest.length / 2)];
  };

  return {
    trace: {
      points,
    },
    metrics: {
      averageDeviationDb: average,
      maxDeviationDb: maximum,
      db10k: nearest(10000),
      db18k: nearest(18000),
    },
  };
}

function measureJump(mode) {
  const engine = makeEngine(mode);
  engine.setFeedback(0.995);
  engine.setMix(1);
  engine.setCrossfadeMs(JUMP_CROSSFADE_MS);

  const duration = 1.2;
  const length = Math.round(duration * SAMPLE_RATE);
  const input = new Float32Array(length);
  const output = new Float32Array(length);
  const frequency = 1700;
  const amplitude = 0.22;
  const jumps = [8, 42, 13, 30, 5, 24, 47, 11];
  const jumpInterval = Math.round(0.09 * SAMPLE_RATE);
  const firstJump = Math.round(0.16 * SAMPLE_RATE);
  const eventSamples = jumps.map((_, index) => firstJump + index * jumpInterval);
  let eventIndex = 0;
  let maxDelta = 0;
  let maxAbs = 0;
  let nonFinite = 0;

  for (let index = 0; index < length; index += 1) {
    if (eventIndex < eventSamples.length && index === eventSamples[eventIndex]) {
      engine.setDelayMs(jumps[eventIndex]);
      eventIndex += 1;
    }
    input[index] = Math.sin((2 * Math.PI * frequency * index) / SAMPLE_RATE) * amplitude;
    output[index] = engine.processSample(input[index]);
    if (!Number.isFinite(output[index])) {
      nonFinite += 1;
    } else {
      maxAbs = Math.max(maxAbs, Math.abs(output[index]));
      if (index > 0) maxDelta = Math.max(maxDelta, Math.abs(output[index] - output[index - 1]));
    }
  }

  const baselineDelta = amplitude * ((2 * Math.PI * frequency) / SAMPLE_RATE);
  const points = 240;
  const bucket = Math.floor(length / points);
  const envelope = [];
  for (let point = 0; point < points; point += 1) {
    let localMax = 0;
    const start = point * bucket;
    for (let index = start; index < Math.min(length, start + bucket); index += 1) {
      localMax = Math.max(localMax, Math.abs(output[index]));
    }
    envelope.push(localMax / amplitude);
  }

  return {
    trace: {
      values: envelope,
      duration,
      events: eventSamples.map((sample) => sample / SAMPLE_RATE),
      targets: jumps,
    },
    metrics: {
      maxDelta: maxDelta / baselineDelta,
      peakGain: maxAbs / amplitude,
      maxAbs,
      stable: nonFinite === 0 && maxAbs < 4,
      nonFinite,
      feedback: 0.995,
    },
  };
}

export async function runMeasurementSuite(onProgress = () => {}) {
  const impulse = {
    linear: measureImpulse("linear"),
    cubic: measureImpulse("cubic"),
    sampleRate: SAMPLE_RATE,
    delayMs: TEST_DELAY_MS,
    crossfadeMs: JUMP_CROSSFADE_MS,
  };
  onProgress("impulse", impulse);
  await yieldToBrowser();

  const sweep = {
    linear: measureSweep("linear"),
    cubic: measureSweep("cubic"),
    sampleRate: SAMPLE_RATE,
    delayMs: TEST_DELAY_MS,
  };
  onProgress("sweep", sweep);
  await yieldToBrowser();

  const jump = {
    linear: measureJump("linear"),
    cubic: measureJump("cubic"),
    sampleRate: SAMPLE_RATE,
    crossfadeMs: JUMP_CROSSFADE_MS,
  };
  onProgress("jump", jump);
  await yieldToBrowser();

  return { impulse, sweep, jump, completedAt: Date.now() };
}
