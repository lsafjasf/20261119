import { FailureReason } from "./errors.js";
import { PlayerState } from "./states.js";

export class PlaybackTerminatedError extends Error {
  constructor(reason, attempts) {
    super(`playback terminated after ${attempts} consecutive failures (${reason})`);
    this.name = "PlaybackTerminatedError";
    this.reason = reason;
    this.attempts = attempts;
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Drives the RECOVERING / FALLING_BACK / TERMINATED portion of the state
// machine. The player supplies an `attemptLoad` callback that performs one
// concrete segment fetch+decode at a given bitrate.
export class RecoveryEngine {
  constructor({
    logger,
    bitrateLadder,
    maxConsecutiveFailures = 3,
    backoffBaseMs = 50,
    sleeper = sleep,
    clock = Date,
  }) {
    if (!Array.isArray(bitrateLadder) || bitrateLadder.length === 0) {
      throw new Error("bitrateLadder must be a non-empty array");
    }
    this.logger = logger;
    this.bitrateLadder = [...bitrateLadder].sort((a, b) => a - b);
    this.maxConsecutiveFailures = maxConsecutiveFailures;
    this.backoffBaseMs = backoffBaseMs;
    this.sleeper = sleeper;
    this.clock = clock;
    this.state = PlayerState.IDLE;
    this.consecutiveFailures = 0;
    this.terminated = false;
  }

  markPlaying() {
    this._transition(PlayerState.PLAYING);
  }

  _transition(next, extra = {}) {
    const from = this.state;
    this.state = next;
    return this.logger.logTransition({ from, to: next, ...extra });
  }

  _fallbackBitrate(attempt, currentIndex) {
    // The first retry already steps one rung down; later retries keep
    // stepping down and clamp at the lowest rung of the ladder.
    const index = Math.max(0, currentIndex - attempt);
    return { index, bitrate: this.bitrateLadder[index] };
  }

  // Runs retries until one load succeeds or the consecutive-failure cap is
  // reached. `context` describes what failed; `attemptLoad(bitrate, index)`
  // performs a single retry and must return the decoded segment.
  async recover(context, attemptLoad) {
    if (this.terminated) {
      throw new PlaybackTerminatedError("already_terminated", this.consecutiveFailures);
    }
    const { reason, seq, targetTime, bitrateIndex } = context;
    this._transition(PlayerState.RECOVERING, { reason, seq, targetTime });

    let lastError = null;
    for (let attempt = 1; attempt <= this.maxConsecutiveFailures; attempt += 1) {
      const { index, bitrate } = this._fallbackBitrate(attempt, bitrateIndex);
      if (bitrate !== this.bitrateLadder[bitrateIndex]) {
        this._transition(PlayerState.FALLING_BACK, {
          attempt,
          fallbackBitrate: bitrate,
        });
      }

      await this.sleeper(this.backoffBaseMs * 2 ** (attempt - 1));

      try {
        const segment = await attemptLoad(bitrate, index, attempt);
        this.consecutiveFailures = 0;
        this.logger.logRecovery({
          event: "recovery",
          reason,
          seq,
          targetTime,
          attempt,
            retryCount: attempt - 1,
          bitrate,
          result: "success",
        });
        this._transition(PlayerState.PLAYING, { reason: "recovered", attempt });
        return { segment, bitrate, bitrateIndex: index };
      } catch (error) {
        lastError = error;
        this.consecutiveFailures = attempt;
        this.logger.logRecovery({
          event: "recovery",
          reason: error.reason || reason,
          seq,
          targetTime,
          attempt,
          retryCount: attempt - 1,
          bitrate,
          result: "failed",
          detail: error.message,
        });
      }
    }

    // Cap reached: exactly one termination event, then stop for good.
    this.terminated = true;
    this._transition(PlayerState.TERMINATED, {
      reason: lastError ? lastError.reason || reason : reason,
      attempts: this.consecutiveFailures,
    });
    this.logger.logTermination({
      reason: lastError ? lastError.reason || reason : reason,
      seq,
      targetTime,
      attempts: this.consecutiveFailures,
      lastError: lastError ? lastError.message : null,
    });
    throw new PlaybackTerminatedError(
      lastError ? lastError.reason || reason : reason,
      this.consecutiveFailures,
    );
  }
}
