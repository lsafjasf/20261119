export const MAX_FEEDBACK = 0.995;
export const MAX_DELAY_MS = 480;
export const MIN_DELAY_MS = 0.5;

export function clamp(value, min, max) {
  return value < min ? min : value > max ? max : value;
}

export function sanitize(value, fallback = 0) {
  return Number.isFinite(value) ? value : fallback;
}

export function nextPowerOfTwo(value) {
  let size = 1;
  while (size < value) size *= 2;
  return size;
}

export function linearTap(buffer, mask, writePosition, delaySamples) {
  const position = writePosition - delaySamples;
  const integer = Math.floor(position);
  const fraction = position - integer;
  const index = integer & mask;
  return (
    buffer[index] * (1 - fraction) +
    buffer[(index + 1) & mask] * fraction
  );
}

export function cubicTap(buffer, mask, writePosition, delaySamples) {
  const position = writePosition - delaySamples;
  const integer = Math.floor(position);
  const x = position - integer;
  const index = integer & mask;
  const y0 = buffer[(index - 1) & mask];
  const y1 = buffer[index];
  const y2 = buffer[(index + 1) & mask];
  const y3 = buffer[(index + 2) & mask];

  const x2 = x * x;
  const x3 = x2 * x;
  const c0 = (-x3 + 3 * x2 - 2 * x) / 6;
  const c1 = (x3 - 2 * x2 - x + 2) / 2;
  const c2 = (-x3 + x2 + 2 * x) / 2;
  const c3 = (x3 - x) / 6;
  return y0 * c0 + y1 * c1 + y2 * c2 + y3 * c3;
}

export function interpolateTap(buffer, mask, writePosition, delaySamples, mode) {
  return mode === "cubic"
    ? cubicTap(buffer, mask, writePosition, delaySamples)
    : linearTap(buffer, mask, writePosition, delaySamples);
}

export function softClip(value, threshold = 0.82) {
  const magnitude = Math.abs(value);
  if (magnitude <= threshold) return value;
  const clipped = threshold + (1 - threshold) * Math.tanh((magnitude - threshold) / (1 - threshold));
  return Math.sign(value) * clipped;
}

export function softLimitOutput(value, threshold = 1.05, ceiling = 1.2) {
  const magnitude = Math.abs(value);
  if (magnitude <= threshold) return value;
  const clipped = threshold + (ceiling - threshold) * Math.tanh((magnitude - threshold) / (ceiling - threshold));
  return Math.sign(value) * clipped;
}

export class DelayEngine {
  constructor({
    sampleRate = 48000,
    minDelayMs = MIN_DELAY_MS,
    maxDelayMs = MAX_DELAY_MS,
    feedback = 0.35,
    mix = 1,
    interpolation = "cubic",
    crossfadeMs = 28,
  } = {}) {
    this.sampleRate = sampleRate;
    this.minDelaySamples = Math.max(4, (minDelayMs / 1000) * sampleRate);
    this.maxDelaySamples = (maxDelayMs / 1000) * sampleRate;
    this.size = nextPowerOfTwo(Math.ceil(sampleRate * 0.6));
    this.mask = this.size - 1;
    this.buffer = new Float32Array(this.size);
    this.writePosition = 0;
    this.feedback = clamp(feedback, 0, MAX_FEEDBACK);
    this.mix = clamp(mix, 0, 1);
    this.interpolation = interpolation === "linear" ? "linear" : "cubic";
    this.crossfadeSamples = Math.max(1, (crossfadeMs / 1000) * sampleRate);
    this.activeIndex = 0;
    this.heads = [
      { delaySamples: (23.4 / 1000) * sampleRate },
      { delaySamples: (23.4 / 1000) * sampleRate },
    ];
    this.targetDelaySamples = this.heads[0].delaySamples;
    this.pendingDelaySamples = null;
    this.fading = false;
    this.fade = 1;
    this.feedbackClamps = 0;
    this.nonFiniteResets = 0;
    this.lastInput = 0;
    this.lastWet = 0;
    this.lastOutput = 0;
    this.lastReadA = 0;
    this.lastReadB = 0;
  }

  secondsToSamples(seconds) {
    const ms = clamp(seconds * 1000, this.minDelaySamples * 1000 / this.sampleRate, MAX_DELAY_MS);
    return (ms / 1000) * this.sampleRate;
  }

  setDelayMs(milliseconds, { immediate = false } = {}) {
    const target = clamp(
      (milliseconds / 1000) * this.sampleRate,
      this.minDelaySamples,
      this.maxDelaySamples,
    );
    this.targetDelaySamples = target;

    if (immediate || !this.fading) {
      const current = this.heads[this.activeIndex].delaySamples;
      if (Math.abs(current - target) < 1e-7) {
        this.heads[this.activeIndex].delaySamples = target;
        return;
      }
      if (immediate) {
        this.flush(target);
        return;
      }
      this.beginCrossfade(target);
      return;
    }

    this.pendingDelaySamples = target;
  }

  setFeedback(value) {
    this.feedback = clamp(sanitize(value, 0), 0, MAX_FEEDBACK);
  }

  setMix(value) {
    this.mix = clamp(sanitize(value, 1), 0, 1);
  }

  setInterpolation(mode) {
    this.interpolation = mode === "linear" ? "linear" : "cubic";
  }

  setCrossfadeMs(milliseconds) {
    this.crossfadeSamples = Math.max(1, (clamp(milliseconds, 1, 500) / 1000) * this.sampleRate);
  }

  beginCrossfade(target) {
    const standbyIndex = 1 - this.activeIndex;
    this.heads[standbyIndex].delaySamples = target;
    this.fade = 0;
    this.fading = true;
    this.pendingDelaySamples = null;
  }

  get effectiveDelaySamples() {
    if (!this.fading) return this.heads[this.activeIndex].delaySamples;
    const outgoing = this.heads[this.activeIndex].delaySamples;
    const incoming = this.heads[1 - this.activeIndex].delaySamples;
    return outgoing * (1 - this.fade) + incoming * this.fade;
  }

  get effectiveDelayMs() {
    return (this.effectiveDelaySamples / this.sampleRate) * 1000;
  }

  readHead(index) {
    const head = this.heads[index];
    return interpolateTap(
      this.buffer,
      this.mask,
      this.writePosition,
      head.delaySamples,
      this.interpolation,
    );
  }

  readAtDelaySamples(delaySamples) {
    return interpolateTap(
      this.buffer,
      this.mask,
      this.writePosition,
      clamp(delaySamples, 1, this.maxDelaySamples),
      this.interpolation,
    );
  }

  processSample(inputSample) {
    const dry = sanitize(inputSample);
    const readA = this.readHead(0);
    const readB = this.readHead(1);
    this.lastReadA = readA;
    this.lastReadB = readB;

    let wet;
    if (this.fading) {
      const outgoing = this.heads[this.activeIndex].delaySamples;
      const incoming = this.heads[1 - this.activeIndex].delaySamples;
      const outgoingGain = 1 - this.fade;
      const incomingGain = this.fade;
      const outgoingSignal = interpolateTap(
        this.buffer,
        this.mask,
        this.writePosition,
        outgoing,
        this.interpolation,
      );
      const incomingSignal = interpolateTap(
        this.buffer,
        this.mask,
        this.writePosition,
        incoming,
        this.interpolation,
      );
      wet = outgoingSignal * outgoingGain + incomingSignal * incomingGain;
    } else {
      wet = this.readHead(this.activeIndex);
    }

    wet = sanitize(wet);
    const feedbackSignal = softClip(wet);
    let written = dry + this.feedback * feedbackSignal;
    if (!Number.isFinite(written)) {
      written = 0;
      this.nonFiniteResets += 1;
    }
    const bounded = clamp(written, -4, 4);
    if (bounded !== written) this.feedbackClamps += 1;
    written = bounded;
    this.buffer[this.writePosition] = written;
    this.writePosition = (this.writePosition + 1) & this.mask;

    const mixed = dry * (1 - this.mix) + wet * this.mix;
    const output = softLimitOutput(sanitize(mixed));
    this.lastInput = dry;
    this.lastWet = wet;
    this.lastOutput = output;

    if (this.fading) {
      this.fade += 1 / this.crossfadeSamples;
      if (this.fade >= 1) {
        this.activeIndex = 1 - this.activeIndex;
        this.fade = 1;
        this.fading = false;
        if (
          this.pendingDelaySamples !== null &&
          Math.abs(this.pendingDelaySamples - this.heads[this.activeIndex].delaySamples) > 1e-7
        ) {
          const pending = this.pendingDelaySamples;
          this.pendingDelaySamples = null;
          this.beginCrossfade(pending);
        }
      }
    }

    return output;
  }

  processBlock(input, output, paused = false) {
    if (paused) {
      output.fill(0);
      return false;
    }
    const length = Math.min(input.length, output.length);
    for (let index = 0; index < length; index += 1) {
      output[index] = this.processSample(input[index]);
    }
    return true;
  }

  flush(targetDelaySamples = this.targetDelaySamples) {
    this.buffer.fill(0);
    this.writePosition = 0;
    this.targetDelaySamples = clamp(
      targetDelaySamples,
      this.minDelaySamples,
      this.maxDelaySamples,
    );
    this.heads[0].delaySamples = this.targetDelaySamples;
    this.heads[1].delaySamples = this.targetDelaySamples;
    this.activeIndex = 0;
    this.fade = 1;
    this.fading = false;
    this.pendingDelaySamples = null;
    this.lastInput = 0;
    this.lastWet = 0;
    this.lastOutput = 0;
  }

  readPositionFor(index) {
    const delay = this.heads[index].delaySamples;
    return (this.writePosition - delay + this.size) % this.size;
  }

  telemetry() {
    const outgoingIndex = this.activeIndex;
    const incomingIndex = 1 - this.activeIndex;
    const gainA = this.fading
      ? outgoingIndex === 0 ? 1 - this.fade : this.fade
      : this.activeIndex === 0 ? 1 : 0;
    const gainB = 1 - gainA;
    return {
      sampleRate: this.sampleRate,
      ringSize: this.size,
      writePosition: this.writePosition,
      readPositionA: this.readPositionFor(0),
      readPositionB: this.readPositionFor(1),
      delayA: this.heads[0].delaySamples,
      delayB: this.heads[1].delaySamples,
      targetDelay: this.targetDelaySamples,
      effectiveDelay: this.effectiveDelaySamples,
      fraction: this.effectiveDelaySamples % 1,
      fading: this.fading,
      fade: this.fade,
      outgoing: this.fading ? outgoingIndex : this.activeIndex,
      incoming: this.fading ? incomingIndex : this.activeIndex,
      gainA,
      gainB,
      activeIndex: this.activeIndex,
      feedbackClamps: this.feedbackClamps,
      nonFiniteResets: this.nonFiniteResets,
      lastReadA: this.lastReadA,
      lastReadB: this.lastReadB,
      lastInput: this.lastInput,
      lastWet: this.lastWet,
      lastOutput: this.lastOutput,
      interpolation: this.interpolation,
    };
  }
}

export class TestSignalGenerator {
  constructor(sampleRate = 48000, mode = "sine") {
    this.sampleRate = sampleRate;
    this.mode = mode;
    this.time = 0;
    this.sweepPhase = 0;
    this.noiseState = 0x12345678;
    this.impulsePeriod = Math.round(sampleRate * 0.72);
  }

  setMode(mode) {
    this.mode = mode;
    this.time = 0;
    this.sweepPhase = 0;
  }

  next() {
    this.time += 1;
    if (this.mode === "sweep") {
      const duration = 7;
      const t = (this.time / this.sampleRate) % duration;
      const frequency = 30 * Math.pow(18000 / 30, t / duration);
      this.sweepPhase += (2 * Math.PI * frequency) / this.sampleRate;
      if (this.sweepPhase > Math.PI * 2) this.sweepPhase -= Math.PI * 2;
      const edge = Math.min(1, t / 0.03, (duration - t) / 0.03);
      return Math.sin(this.sweepPhase) * 0.24 * Math.max(0, edge);
    }
    if (this.mode === "impulse") {
      return this.time % this.impulsePeriod === 0 ? 0.82 : 0;
    }
    if (this.mode === "noise") {
      let state = this.noiseState;
      state ^= state << 13;
      state ^= state >>> 17;
      state ^= state << 5;
      this.noiseState = state >>> 0;
      return ((this.noiseState / 0xffffffff) * 2 - 1) * 0.12;
    }
    return Math.sin((2 * Math.PI * 220 * this.time) / this.sampleRate) * 0.28;
  }

  nextBlock(length, output) {
    for (let index = 0; index < length; index += 1) {
      output[index] = this.next();
    }
    return output;
  }
}
