import { EventEmitter } from "node:events";
import { FailureReason } from "../recovery/errors.js";
import { PlaybackTerminatedError, RecoveryEngine } from "../recovery/recovery-engine.js";
import { PlayerState } from "../recovery/states.js";

// Simulated adaptive-bitrate player. Real wall-clock timers drive a virtual
// presentation clock; media "decoding" is the protocol-level segment parse.
// Playback position never regresses: recovery resumes from the last decodable
// segment and catches up toward the target time.
export class SimulatedPlayer extends EventEmitter {
  constructor({
    client,
    logger,
    manifest,
    maxConsecutiveFailures = 3,
    backoffBaseMs = 50,
    tickIntervalMs = 20,
    tickStepSec = 0.1,
    catchUpWindowSec = manifest.segmentDuration * 2,
    clock = Date,
    sleeper,
  }) {
    super();
    this.client = client;
    this.logger = logger;
    this.manifest = manifest;
    this.segmentDuration = manifest.segmentDuration;
    this.bitrateLadder = manifest.bitrates;
    this.segmentCount = manifest.segmentCount;
    this.bitrateIndex = this.bitrateLadder.length - 1;
    this.tickIntervalMs = tickIntervalMs;
    this.tickStepSec = tickStepSec;
    this.catchUpWindowSec = catchUpWindowSec;

    this.engine = new RecoveryEngine({
      logger,
      bitrateLadder: this.bitrateLadder,
      maxConsecutiveFailures,
      backoffBaseMs,
      clock,
      ...(sleeper ? { sleeper } : {}),
    });

    this.buffer = [];
    this.nextSeq = 0;
    this.currentTime = 0;
    this.lastDecodedTime = 0;
    this.lastDecodableTime = 0;
    this.targetTime = 0;
    this.running = false;
    this.pumping = false;
    this.starvationSignaled = false;
    this.timer = null;
    this.prefetchLimit = manifest.segmentDuration * 3;
  }

  get state() {
    return this.engine.state;
  }

  get consecutiveFailures() {
    return this.engine.consecutiveFailures;
  }

  async start() {
    this.running = true;
    this.engine.markPlaying();
    this.emit("state", this.state);
    this.timer = setInterval(() => this._tick(), this.tickIntervalMs);
    this.timer.unref?.();
    this.targetTimer = setInterval(() => this.advanceTarget(this.tickStepSec), this.tickIntervalMs);
    this.targetTimer.unref?.();
    await this._pump();
  }

  stop() {
    this.running = false;
    if (this.timer) clearInterval(this.timer);
    if (this.targetTimer) clearInterval(this.targetTimer);
    this.timer = null;
    this.targetTimer = null;
  }

  _tick() {
    if (!this.running || this.state !== PlayerState.PLAYING) return;
    const bufferedUntil = this.lastDecodedTime;
    if (bufferedUntil <= 0) {
      this._onStarvation(0);
      return;
    }
    const candidate = this.currentTime + this.tickStepSec;
    // Virtual playback may not cross undecoded data.
    if (candidate <= bufferedUntil + this.tickStepSec / 2) {
      this.currentTime = candidate;
      this.targetTime = Math.max(this.targetTime, this.currentTime);
      this.starvationSignaled = false;
      this.emit("time", this.currentTime);
      if (!this.pumping && this.running && this.nextSeq < this.segmentCount) {
        this._pump().catch((error) => this._handleTerminal(error));
      }
    } else {
      this._onStarvation(bufferedUntil);
    }
  }

  // While recovery is in flight the presentation clock is frozen, but the
  // viewer's desired position keeps advancing. Recovery catches up to it.
  advanceTarget(deltaSec) {
    if (this.state === PlayerState.RECOVERING || this.state === PlayerState.FALLING_BACK) {
      this.targetTime += deltaSec;
    }
  }

  _onStarvation(bufferedUntil) {
    if (this.state !== PlayerState.PLAYING || this.starvationSignaled) return;
    this.starvationSignaled = true;
    this.logger.logTransition({
      from: PlayerState.PLAYING,
      to: PlayerState.RECOVERING,
      reason: FailureReason.BUFFER_STARVATION,
      currentTime: this.currentTime,
      bufferedUntil,
    });
    this.emit("stall", {
      reason: FailureReason.BUFFER_STARVATION,
      currentTime: this.currentTime,
    });
    this._pump().catch((error) => this._handleTerminal(error));
  }

  async _pump() {
    if (!this.running || this.pumping) return;
    this.pumping = true;
    try {
      while (this.running) {
        if (this.state === PlayerState.TERMINATED) break;
        if (this.nextSeq >= this.segmentCount) {
          this.logger.logRecovery({ event: "ended", nextSeq: this.nextSeq });
          this.emit("ended", { currentTime: this.currentTime });
          break;
        }
        if (this.lastDecodedTime - this.currentTime >= this.prefetchLimit) {
          // Enough buffered media; the tick restarts the pump as time advances.
          break;
        }

        const seq = this.nextSeq;
        try {
          const segment = await this.client.getSegment(
            seq,
            this.bitrateLadder[this.bitrateIndex],
          );
          this._ingest(segment);
        } catch (error) {
          await this._recoverNext({ reason: error.reason, seq, error });
        }
      }
    } finally {
      this.pumping = false;
      // A stall may have been signalled while the pump was busy in recovery.
      if (this.running && this.starvationSignaled && this.state === PlayerState.PLAYING) {
        this.starvationSignaled = false;
        this._pump().catch((error) => this._handleTerminal(error));
      }
    }
  }

  async _recoverNext({ reason, seq = this.nextSeq, error = null }) {
    // The resume anchor must never run ahead of the play head.
    const anchorBeforeRecovery = Math.min(this.lastDecodableTime, this.currentTime);
    if (error) {
      this.emit("failure", {
        reason: error.reason || reason,
        seq,
        detail: error.message,
      });
    }
    const result = await this.engine.recover(
      {
        reason: reason || (error && error.reason),
        seq,
        targetTime: this.targetTime,
        bitrateIndex: this.bitrateIndex,
      },
      (bitrate, index, attempt) =>
        this.client.getSegment(seq, bitrate).then((segment) => {
          this.emit("retry", { seq, attempt, bitrate, index });
          return segment;
        }),
    );
    this.bitrateIndex = result.bitrateIndex;
    this._ingest(result.segment);
    this.emit("recovered", {
      seq,
      bitrate: result.bitrate,
      currentTime: this.currentTime,
      targetTime: this.targetTime,
      anchorTime: anchorBeforeRecovery,
    });
    await this._catchUp(anchorBeforeRecovery);
  }

  async _catchUp(anchorOverride) {
    const anchor = anchorOverride ?? this.lastDecodedTime;
    const target = this.targetTime;
    let jumped = false;
    if (target - anchor > this.catchUpWindowSec) {
      // Large gap: skip undecodable middle segments and resume at the
      // segment covering the target time. The clock stays at the anchor.
      this.nextSeq = Math.max(
        this.nextSeq,
        Math.floor(target / this.segmentDuration),
      );
      jumped = true;
    }

    let fetched = 0;
    while (
      this.running
      && this.lastDecodedTime < target + this.tickStepSec
      && this.lastDecodedTime - anchor <= this.catchUpWindowSec
    ) {
      const seq = this.nextSeq;
      try {
        const segment = await this.client.getSegment(
          seq,
          this.bitrateLadder[this.bitrateIndex],
        );
        this._ingest(segment);
      } catch (error) {
        await this._recoverNext({ reason: error.reason, seq, error });
        // Recovery may have jumped the pipeline; re-evaluate the loop guard.
      }
      fetched += 1;
    }

    if (fetched > 0 || jumped || target > anchor) {
      this.logger.logRecovery({
        event: "catch_up",
        anchorTime: anchor,
        targetTime: target,
        resumedFrom: anchor,
        jumped,
        fetched,
        nextSeq: this.nextSeq,
      });
      this.emit("caught_up", {
        anchorTime: anchor,
        targetTime: target,
        jumped,
        fetched,
      });
    }
  }

  _ingest(segment) {
    if (segment.seq !== this.nextSeq) {
      // Out-of-order / duplicate data is dropped; timeline must stay monotonic.
      if (segment.seq < this.nextSeq) return;
      this.nextSeq = segment.seq;
    }
    this.buffer.push(segment);
    this.nextSeq = segment.seq + 1;
    this.lastDecodedTime = segment.start + segment.duration;
    // The latest safe resume point is the start of the newest decoded segment;
    // resuming there never replays from zero and stays behind the play head.
    this.lastDecodableTime = Math.max(this.lastDecodableTime, segment.start);
    this.targetTime = Math.max(this.targetTime, this.currentTime);
    this.emit("segment", segment);
  }

  _handleTerminal(error) {
    if (error instanceof PlaybackTerminatedError) {
      this.stop();
      this.emit("terminated", {
        reason: error.reason,
        attempts: error.attempts,
        currentTime: this.currentTime,
      });
      return;
    }
    this.emit("error", error);
  }
}
