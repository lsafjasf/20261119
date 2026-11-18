import { test } from "node:test";
import assert from "node:assert/strict";
import { TransitionLogger } from "../src/recovery/transition-logger.js";
import { RecoveryEngine, PlaybackTerminatedError } from "../src/recovery/recovery-engine.js";
import { FailureReason, SegmentTimeoutError } from "../src/recovery/errors.js";
import { PlayerState } from "../src/recovery/states.js";

function makeEngine({ maxConsecutiveFailures = 3, ladder = [200, 500, 1000] } = {}) {
  const logger = new TransitionLogger();
  const engine = new RecoveryEngine({
    logger,
    bitrateLadder: ladder,
    maxConsecutiveFailures,
    backoffBaseMs: 1,
    sleeper: async () => {},
  });
  return { logger, engine };
}

function context(overrides = {}) {
  return {
    reason: FailureReason.SEGMENT_TIMEOUT,
    seq: 3,
    targetTime: 3.2,
    bitrateIndex: 2,
    ...overrides,
  };
}

test("retries step down the bitrate ladder and recover without replay", async () => {
  const { logger, engine } = makeEngine();
  engine.markPlaying();
  const attemptedBitrates = [];
  const result = await engine.recover(context(), (bitrate, index) => {
    attemptedBitrates.push(bitrate);
    if (attemptedBitrates.length < 2) {
      throw new SegmentTimeoutError(3, 800);
    }
    return Promise.resolve({
      segment: { seq: 3, start: 3, duration: 1 },
      bitrate,
      bitrateIndex: index,
    });
  });

  assert.deepEqual(attemptedBitrates, [500, 200]);
  assert.equal(result.bitrate, 200);
  assert.equal(engine.state, PlayerState.PLAYING);
  assert.equal(engine.consecutiveFailures, 0);

  const records = logger.recoveryRecords.filter((r) => r.event === "recovery");
  assert.equal(records[0].retryCount, 0);
  assert.equal(records[0].result, "failed");
  const success = records.find((r) => r.result === "success");
  assert.equal(success.result, "success");
  assert.equal(success.bitrate, 200);
  assert.ok(records.every((r) => r.reason === FailureReason.SEGMENT_TIMEOUT));

  const fallback = logger.transitions.find((t) => t.to === PlayerState.FALLING_BACK);
  assert.equal(fallback.fallbackBitrate, 500);
});

test("cap reached terminates with exactly one termination event", async () => {
  const { logger, engine } = makeEngine({ maxConsecutiveFailures: 3 });
  engine.markPlaying();
  await assert.rejects(
    engine.recover(context(), () => {
      throw new SegmentTimeoutError(3, 800);
    }),
    PlaybackTerminatedError,
  );

  assert.equal(engine.state, PlayerState.TERMINATED);
  assert.equal(engine.terminated, true);
  const terminations = logger.recoveryRecords.filter((r) => r.event === "terminated");
  assert.equal(terminations.length, 1);
  assert.equal(terminations[0].attempts, 3);

  // Post-termination recoveries must not emit another termination event.
  await assert.rejects(
    engine.recover(context(), () => Promise.resolve({ segment: {} })),
    /already_terminated/,
  );
  assert.equal(
    logger.recoveryRecords.filter((r) => r.event === "terminated").length,
    1,
  );
});

test("successful recovery resets consecutive failure counter", async () => {
  const { engine } = makeEngine({ maxConsecutiveFailures: 2 });
  engine.markPlaying();
  let calls = 0;
  await engine.recover(context(), () => {
    calls += 1;
    if (calls === 1) throw new SegmentTimeoutError(3, 1);
    return Promise.resolve({ seq: 3, start: 3, duration: 1 });
  });
  assert.equal(engine.consecutiveFailures, 0);

  calls = 0;
  const result = await engine.recover(context(), (bitrate, index) => {
    calls += 1;
    if (calls === 1) throw new SegmentTimeoutError(3, 1);
    return Promise.resolve({ seq: 4, start: 4, duration: 1 });
  });
  assert.equal(result.segment.seq, 4);
  assert.equal(engine.consecutiveFailures, 0);
});

test("bitrate clamps at the lowest ladder rung", async () => {
  const { engine } = makeEngine({ maxConsecutiveFailures: 4, ladder: [200, 1000] });
  engine.markPlaying();
  const seen = [];
  await assert.rejects(
    engine.recover(
      { ...context(), bitrateIndex: 1 },
      (bitrate) => {
        seen.push(bitrate);
        throw new SegmentTimeoutError(3, 1);
      },
    ),
    PlaybackTerminatedError,
  );
  assert.deepEqual(seen, [200, 200, 200, 200]);
});
