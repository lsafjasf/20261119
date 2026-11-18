import { BufferMeter } from "./meter.js";
import { decodePlanar } from "./pcm.js";
import {
  DEFAULT_BLOCK_FRAMES,
  DEFAULT_PREFETCH_BLOCKS,
  mapOutputFrameToSource,
  normalizeRegion,
  streamEffectPcm,
} from "./reverse.js";

const SCHEDULE_HORIZON_SECONDS = 1.8;

export class ReversePlayer {
  constructor(callbacks = {}) {
    this.callbacks = callbacks;
    this.source = null;
    this.format = null;
    this.region = { startFrame: 0, endFrame: 0 };
    this.blockFrames = DEFAULT_BLOCK_FRAMES;
    this.prefetchBlocks = DEFAULT_PREFETCH_BLOCKS;
    this.loop = false;
    this.currentFrame = 0;
    this.playing = false;
    this.context = null;
    this.gain = null;
    this.meter = new BufferMeter();
    this.generation = 0;
    this.nodes = new Set();
    this.scheduledAudioBytes = 0;
    this.peakScheduledAudioBytes = 0;
    this.scheduledUntilFrame = 0;
    this.playbackOriginFrame = 0;
    this.playbackOriginTime = 0;
    this.streamDone = false;
    this.animationFrame = 0;
    this.starting = false;
  }

  setAudio(source, format) {
    this.stop();
    this.source = source;
    this.format = format;
    this.region = normalizeRegion(null, format.frames);
    this.currentFrame = 0;
    this.meter = new BufferMeter();
    this.scheduledAudioBytes = 0;
    this.peakScheduledAudioBytes = 0;
    this.emitCursor();
  }

  configure(options = {}) {
    if (options.region) {
      this.region = normalizeRegion(options.region, this.format.frames);
    }
    if (options.blockFrames) {
      this.blockFrames = Math.max(1, Math.floor(options.blockFrames));
    }
    if (options.prefetchBlocks != null) {
      this.prefetchBlocks = Math.max(0, Math.floor(options.prefetchBlocks));
    }
    if (options.loop != null) this.loop = Boolean(options.loop);
    this.emitCursor();
  }

  get totalFrames() {
    return this.format?.frames || 0;
  }

  ensureContext() {
    if (!this.context) {
      this.context = new AudioContext({ latencyHint: "interactive" });
      this.gain = this.context.createGain();
      this.gain.gain.value = 0.85;
      this.gain.connect(this.context.destination);
    }
    return this.context.resume();
  }

  estimateOutputFrame() {
    if (!this.playing || !this.context) return this.currentFrame;
    const estimated = this.playbackClockFrame();
    return Math.max(
      this.playbackOriginFrame,
      Math.min(this.scheduledUntilFrame, estimated),
    );
  }

  playbackClockFrame() {
    if (!this.playing || !this.context) return this.currentFrame;
    const elapsed = Math.max(0, this.context.currentTime - this.playbackOriginTime);
    return this.playbackOriginFrame + elapsed * this.format.sampleRate;
  }

  async play(fromFrame = this.currentFrame) {
    if (!this.source || !this.format || this.starting) return;
    if (this.playing) return;
    if (fromFrame >= this.totalFrames - 1) fromFrame = 0;
    this.starting = true;
    try {
      await this.ensureContext();
      if (this.playing || !this.source) return;
      this.startPlayback(Math.max(0, Math.floor(fromFrame)));
    } finally {
      this.starting = false;
    }
  }

  startPlayback(fromFrame) {
    this.generation += 1;
    const generation = this.generation;
    this.playing = true;
    this.streamDone = false;
    this.currentFrame = fromFrame;
    this.playbackOriginFrame = fromFrame;
    this.playbackOriginTime = this.context.currentTime + 0.08;
    this.scheduledUntilFrame = fromFrame;
    this.emitState();
    this.emitCursor();
    this.startCursorLoop(generation);
    void this.pump(generation, fromFrame);
  }

  async pump(generation, fromFrame) {
    const iterator = streamEffectPcm(this.source, this.format, {
      region: this.region,
      blockFrames: this.blockFrames,
      prefetchBlocks: this.prefetchBlocks,
      startFrame: fromFrame,
      meter: this.meter,
    });
    let nextStartTime = this.playbackOriginTime;
    let scheduledFrame = fromFrame;

    try {
      while (generation === this.generation && this.playing) {
        const aheadFrames = scheduledFrame - this.playbackClockFrame();
        if (aheadFrames > SCHEDULE_HORIZON_SECONDS * this.format.sampleRate) {
          await new Promise((resolve) => setTimeout(resolve, 80));
          continue;
        }

        const { value, done } = await iterator.next();
        if (generation !== this.generation || !this.playing) break;
        if (done) {
          this.streamDone = true;
          break;
        }

        const frameCount = value.byteLength / this.format.blockAlign;
        const planar = decodePlanar(value, this.format, frameCount);
        const audioBuffer = this.context.createBuffer(
          this.format.channels,
          frameCount,
          this.format.sampleRate,
        );
        for (let channel = 0; channel < planar.length; channel += 1) {
          audioBuffer.copyToChannel(planar[channel], channel);
        }

        const node = this.context.createBufferSource();
        node.buffer = audioBuffer;
        node.connect(this.gain);
        const startAt = Math.max(this.context.currentTime + 0.015, nextStartTime);
        node.start(startAt);
        nextStartTime = startAt + frameCount / this.format.sampleRate;
        scheduledFrame += frameCount;
        this.scheduledUntilFrame = scheduledFrame;
        this.nodes.add(node);
        const heldBytes = frameCount * this.format.channels * 4;
        this.scheduledAudioBytes += heldBytes;
        this.peakScheduledAudioBytes = Math.max(
          this.peakScheduledAudioBytes,
          this.scheduledAudioBytes,
        );
        node.onended = () => {
          this.nodes.delete(node);
          node.disconnect();
          this.scheduledAudioBytes = Math.max(0, this.scheduledAudioBytes - heldBytes);
        };
      }
    } catch (error) {
      if (generation === this.generation) {
        this.fail(error);
      }
    } finally {
      try {
        await iterator.return();
      } catch {
        // The primary playback error is reported by catch above.
      }
    }
  }

  startCursorLoop(generation) {
    cancelAnimationFrame(this.animationFrame);
    const tick = () => {
      if (!this.playing || generation !== this.generation) return;
      const estimated = this.estimateOutputFrame();
      this.currentFrame = Math.min(this.totalFrames, Math.round(estimated));
      this.emitCursor();
      if (this.streamDone && estimated >= this.totalFrames - 0.5) {
        this.finishPlayback();
        return;
      }
      this.animationFrame = requestAnimationFrame(tick);
    };
    this.animationFrame = requestAnimationFrame(tick);
  }

  finishPlayback() {
    cancelAnimationFrame(this.animationFrame);
    this.generation += 1;
    this.playing = false;
    this.currentFrame = this.totalFrames;
    if (this.loop) {
      this.currentFrame = 0;
      this.startPlayback(0);
      return;
    }
    this.stopNodes();
    this.emitState();
    this.emitCursor();
  }

  pause() {
    if (!this.playing) return;
    const frame = Math.min(this.totalFrames, Math.round(this.estimateOutputFrame()));
    this.generation += 1;
    this.playing = false;
    this.currentFrame = frame;
    cancelAnimationFrame(this.animationFrame);
    this.stopNodes();
    this.emitState();
    this.emitCursor();
  }

  stop() {
    this.generation += 1;
    this.playing = false;
    this.streamDone = false;
    cancelAnimationFrame(this.animationFrame);
    this.stopNodes();
    this.emitState();
  }

  stopNodes() {
    for (const node of this.nodes) {
      try {
        node.stop();
      } catch {
        // A source can already have ended.
      }
      node.disconnect();
    }
    this.nodes.clear();
    this.scheduledAudioBytes = 0;
  }

  async seek(frame) {
    const target = Math.max(0, Math.min(this.totalFrames, Math.floor(frame)));
    const resume = this.playing;
    if (resume) {
      this.generation += 1;
      this.playing = false;
      cancelAnimationFrame(this.animationFrame);
      this.stopNodes();
    }
    this.currentFrame = target;
    this.emitCursor();
    if (resume) {
      await this.play(target);
    }
  }

  fail(error) {
    this.stop();
    this.callbacks.onError?.(error);
  }

  emitState() {
    this.callbacks.onState?.({
      playing: this.playing,
      outputFrame: this.currentFrame,
      totalFrames: this.totalFrames,
    });
  }

  emitCursor() {
    if (!this.format) return;
    const sourceFrame = mapOutputFrameToSource(
      this.currentFrame,
      this.totalFrames,
      this.region,
    );
    const memory = this.meter.snapshot();
    this.callbacks.onCursor?.({
      playing: this.playing,
      outputFrame: this.currentFrame,
      sourceFrame,
      totalFrames: this.totalFrames,
      region: this.region,
      prefetchPeakBytes: memory.peakHeldBytes,
      heldBytes: memory.heldBytes,
      scheduledAudioBytes: this.scheduledAudioBytes,
      peakScheduledAudioBytes: this.peakScheduledAudioBytes,
    });
  }
}

