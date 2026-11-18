import { clamp, readWrappedLinear } from "./dsp-utils.js";
import { normalizeWindowType, windowValue } from "./windows.js";

export const OVERLOAD_POLICY = "reject-new";

export const ENGINE_LIMITS = Object.freeze({
  minGrainSeconds: 0.002,
  maxGrainSeconds: 8,
  minIntervalSeconds: 0.001,
  maxIntervalSeconds: 4,
  minVoices: 1,
  maxVoices: 256,
  maxScanRate: 2
});

export class GranularEngine {
  constructor(options = {}) {
    this.sampleRate = options.sampleRate || 48000;
    this.source = new Float32Array(0);
    this.grainLengthSeconds = 0.09;
    this.emissionIntervalSeconds = 0.03;
    this.windowType = "hann";
    this.maxVoices = 32;
    this.masterGain = 0.8;
    this.scanRate = 1;
    this.clipEnabled = true;
    this.frozen = false;
    this.readPositionSeconds = 0;
    this.frozenPositionSeconds = 0;
    this.resetCounters();
    this.clearGrains();
    this._playing = false;
    this._outputFrame = 0;
    this._samplesToNextTrigger = 0;
    this.grainRate = 1;
  }

  get sourceDurationSeconds() {
    return this.source.length / this.sampleRate;
  }

  get positionSeconds() {
    return this.frozen ? this.frozenPositionSeconds : this.readPositionSeconds;
  }

  get isPlaying() {
    return this._playing;
  }

  get activeCount() {
    return this._grains.length;
  }

  get queueLength() {
    return 0;
  }

  get attemptCount() {
    return this._accepted + this._rejected;
  }

  resetCounters() {
    this._accepted = 0;
    this._rejected = 0;
    this._peakActive = 0;
  }

  clearGrains() {
    this._grains = [];
    this._nextGrainId = 1;
  }

  setSource(source, options = {}) {
    this.source = source instanceof Float32Array ? source : Float32Array.from(source || []);
    this._playing = false;
    this.clearGrains();
    if (options.resetPosition !== false) {
      this.readPositionSeconds = 0;
      this.frozenPositionSeconds = 0;
    }
    this._clampPosition();
  }

  setGrainLengthSeconds(seconds) {
    this.grainLengthSeconds = clamp(
      Number(seconds) || ENGINE_LIMITS.minGrainSeconds,
      ENGINE_LIMITS.minGrainSeconds,
      ENGINE_LIMITS.maxGrainSeconds
    );
  }

  setEmissionIntervalSeconds(seconds) {
    const previousSamples = this.intervalSamples;
    this.emissionIntervalSeconds = clamp(
      Number(seconds) || ENGINE_LIMITS.minIntervalSeconds,
      ENGINE_LIMITS.minIntervalSeconds,
      ENGINE_LIMITS.maxIntervalSeconds
    );
    const nextSamples = this.intervalSamples;
    if (previousSamples > 0 && nextSamples < this._samplesToNextTrigger) {
      this._samplesToNextTrigger = nextSamples;
    }
  }

  setWindowType(type) {
    this.windowType = normalizeWindowType(type);
  }

  setMaxVoices(value) {
    this.maxVoices = Math.round(clamp(
      Number(value) || 1,
      ENGINE_LIMITS.minVoices,
      ENGINE_LIMITS.maxVoices
    ));
    while (this._grains.length > this.maxVoices) this._grains.shift();
  }

  setMasterGain(value) {
    this.masterGain = clamp(Number(value) || 0, 0, 2);
  }

  setScanRate(value) {
    this.scanRate = clamp(
      Number(value) || 0,
      -ENGINE_LIMITS.maxScanRate,
      ENGINE_LIMITS.maxScanRate
    );
  }

  setClipEnabled(enabled) {
    this.clipEnabled = Boolean(enabled);
  }

  setFrozen(frozen) {
    const next = Boolean(frozen);
    if (next === this.frozen) return;
    if (next) {
      this.frozenPositionSeconds = this.readPositionSeconds;
      this.frozen = true;
    } else {
      this.readPositionSeconds = this.frozenPositionSeconds;
      this.frozen = false;
    }
  }

  seek(seconds) {
    const next = clamp(Number(seconds) || 0, 0, this.sourceDurationSeconds);
    this.readPositionSeconds = next;
    if (this.frozen) this.frozenPositionSeconds = next;
  }

  play() {
    if (!this.source.length) return false;
    this._playing = true;
    this._outputFrame = 0;
    this._samplesToNextTrigger = 0;
    this.clearGrains();
    this.resetCounters();
    return true;
  }

  stop() {
    this._playing = false;
    this.clearGrains();
    this._samplesToNextTrigger = 0;
  }

  configure(options = {}) {
    if (options.grainLengthSeconds !== undefined) {
      this.setGrainLengthSeconds(options.grainLengthSeconds);
    }
    if (options.emissionIntervalSeconds !== undefined) {
      this.setEmissionIntervalSeconds(options.emissionIntervalSeconds);
    }
    if (options.windowType !== undefined) this.setWindowType(options.windowType);
    if (options.maxVoices !== undefined) this.setMaxVoices(options.maxVoices);
    if (options.masterGain !== undefined) this.setMasterGain(options.masterGain);
    if (options.scanRate !== undefined) this.setScanRate(options.scanRate);
    if (options.clipEnabled !== undefined) this.setClipEnabled(options.clipEnabled);
  }

  get intervalSamples() {
    return Math.max(1, this.emissionIntervalSeconds * this.sampleRate);
  }

  _clampPosition() {
    const duration = this.sourceDurationSeconds;
    this.readPositionSeconds = clamp(this.readPositionSeconds, 0, duration);
    this.frozenPositionSeconds = clamp(this.frozenPositionSeconds, 0, duration);
    if (duration > 0 && this.readPositionSeconds >= duration) this.readPositionSeconds = 0;
    if (duration > 0 && this.frozenPositionSeconds >= duration) this.frozenPositionSeconds = 0;
  }

  _triggerGrain() {
    if (this._grains.length >= this.maxVoices) {
      this._rejected += 1;
      return;
    }
    const lengthFrames = Math.max(1, Math.round(this.grainLengthSeconds * this.sampleRate));
    const grain = {
      id: this._nextGrainId,
      sourceStart: this.positionSeconds * this.sampleRate,
      age: 0,
      length: lengthFrames,
      outputStartFrame: this._outputFrame,
      windowType: this.windowType,
      rate: this.grainRate
    };
    this._nextGrainId += 1;
    this._grains.push(grain);
    this._accepted += 1;
    this._peakActive = Math.max(this._peakActive, this._grains.length);
  }

  process(output, frameCount = output.length) {
    const count = Math.min(frameCount, output.length);
    for (let frame = 0; frame < count; frame += 1) {
      if (this._playing && this._samplesToNextTrigger <= 0) {
        this._triggerGrain();
        this._samplesToNextTrigger += this.intervalSamples;
      }

      let mixed = 0;
      for (let index = this._grains.length - 1; index >= 0; index -= 1) {
        const grain = this._grains[index];
        const sourcePosition = grain.sourceStart + grain.age * grain.rate;
        const window = windowValue(grain.windowType, grain.age / grain.length);
        mixed += readWrappedLinear(this.source, sourcePosition) * window;
        grain.age += 1;
        if (grain.age >= grain.length) this._grains.splice(index, 1);
      }

      let value = mixed * this.masterGain;
      if (this.clipEnabled) value = clamp(value, -1, 1);
      output[frame] = value;

      if (this._playing) {
        this._samplesToNextTrigger -= 1;
        if (!this.frozen) {
          this.readPositionSeconds += this.scanRate / this.sampleRate;
          this._wrapReadPosition();
        }
      }
      this._outputFrame += 1;
    }
    return output;
  }

  _wrapReadPosition() {
    const duration = this.sourceDurationSeconds;
    if (duration <= 0) {
      this.readPositionSeconds = 0;
      return;
    }
    while (this.readPositionSeconds >= duration) {
      this.readPositionSeconds -= duration;
    }
    while (this.readPositionSeconds < 0) {
      this.readPositionSeconds += duration;
    }
  }

  getStatus() {
    return {
      playing: this._playing,
      policy: OVERLOAD_POLICY,
      queueLength: 0,
      activeCount: this._grains.length,
      peakActive: this._peakActive,
      maxVoices: this.maxVoices,
      accepted: this._accepted,
      rejected: this._rejected,
      attempts: this.attemptCount,
      outputFrame: this._outputFrame,
      readPositionSeconds: this.positionSeconds,
      sourceDurationSeconds: this.sourceDurationSeconds,
      frozen: this.frozen,
      grainLengthSeconds: this.grainLengthSeconds,
      emissionIntervalSeconds: this.emissionIntervalSeconds,
      windowType: this.windowType,
      grains: this._grains.map((grain) => ({
        id: grain.id,
        age: grain.age,
        length: grain.length,
        progress: grain.age / grain.length,
        sourceStartSeconds: grain.sourceStart / this.sampleRate,
        outputStartFrame: grain.outputStartFrame
      }))
    };
  }
}
