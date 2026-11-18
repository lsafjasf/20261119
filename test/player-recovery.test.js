import { test, after } from "node:test";
import assert from "node:assert/strict";
import { once } from "node:events";
import { FaultInjectionServer } from "../src/fault/fault-injection-server.js";
import { SegmentClient } from "../src/player/segment-client.js";
import { SimulatedPlayer } from "../src/player/simulated-player.js";
import { TransitionLogger } from "../src/recovery/transition-logger.js";
import { FailureReason } from "../src/recovery/errors.js";
import { PlayerState } from "../src/recovery/states.js";

async function harness({ maxConsecutiveFailures = 3, timeoutMs = 120 } = {}) {
  const server = new FaultInjectionServer({
    segmentCount: 60,
    segmentDuration: 1,
    bitrates: [200, 500, 1000],
    timeoutDelayMs: 1000,
  });
  await server.listen();
  after(async () => server.close());

  const client = new SegmentClient({
    baseUrl: server.baseUrl,
    requestTimeoutMs: timeoutMs,
  });
  const manifest = await client.getManifest();
  const logger = new TransitionLogger();
  const player = new SimulatedPlayer({
    client,
    logger,
    manifest,
    maxConsecutiveFailures,
    backoffBaseMs: 5,
    tickIntervalMs: 10,
    tickStepSec: 0.05,
  });
  return { server, client, logger, player };
}

const waitFor = async (emitter, event, predicate, timeoutMs = 4000) =>
  new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`timeout waiting for ${event}`)), timeoutMs);
    const handler = (payload) => {
      if (!predicate || predicate(payload)) {
        clearTimeout(timer);
        emitter.off(event, handler);
        resolve(payload);
      }
    };
    emitter.on(event, handler);
  });

test("segment timeout: recovery falls back bitrate without restarting timeline", async () => {
  const { server, logger, player } = await harness();
  await player.start();
  await waitFor(player, "segment", (s) => s.seq >= 2);
  const positionBeforeFailure = player.currentTime;

  server.enqueue("timeout", 2);
  const recovered = await waitFor(player, "recovered", (r) => r.seq >= player.nextSeq - 1);

  assert.ok(recovered.bitrate < 1000, "bitrate must fall back from top rung");
  assert.ok(
    player.currentTime >= positionBeforeFailure - 1e-9,
    "playback position must not regress to the beginning",
  );
  assert.equal(player.state, PlayerState.PLAYING);
  assert.equal(player.consecutiveFailures, 0);

  const rec = logger.recoveryRecords.find((r) => r.event === "recovery" && r.result === "success");
  assert.equal(rec.reason, FailureReason.SEGMENT_TIMEOUT);
  assert.ok(rec.retryCount >= 1);
  assert.ok(rec.bitrate <= 500);
  player.stop();
});

test("corrupt media: decode error recovery resumes from last decodable position", async () => {
  const { server, logger, player } = await harness();
  await player.start();
  await waitFor(player, "segment", (s) => s.seq >= 3);
  const anchor = player.lastDecodedTime;
  const failurePromise = waitFor(
    player,
    "failure",
    (f) => f.reason === FailureReason.DECODE_ERROR,
  );
  server.enqueue("corrupt", 1);

  await waitFor(player, "recovered");
  assert.ok(player.lastDecodedTime >= anchor);
  const failure = await failurePromise;
  assert.ok(failure.detail.includes("crc mismatch"));
  const recovery = logger.recoveryRecords.find(
    (r) => r.event === "recovery" && r.reason === FailureReason.DECODE_ERROR,
  );
  assert.ok(recovery, "decode error recovery must be journaled");

  // A later segment still starts after the anchor; nothing replays from zero.
  const later = await waitFor(player, "segment", (s) => s.start > anchor);
  assert.ok(later.start >= anchor);
  player.stop();
});

test("network interruption then recover() resumes and catches up toward target time", async () => {
  const { server, player } = await harness({ maxConsecutiveFailures: 8 });
  await player.start();
  await waitFor(player, "segment", (s) => s.seq >= 2);
  const targetBeforeOutage = player.targetTime;
  // Block every request during the outage so retries keep failing until the
  // link is explicitly restored, producing a real gap to catch up.
  server.enqueue("reset", 10);
  await waitFor(player, "failure", (f) => f.reason === FailureReason.NETWORK_INTERRUPTION);

  // While recovering, let the virtual target advance, then restore the link.
  await new Promise((resolve) => setTimeout(resolve, 60));
  server.recover();
  const caught = await waitFor(player, "caught_up");
  assert.ok(caught.targetTime >= targetBeforeOutage);
  assert.ok(caught.anchorTime >= targetBeforeOutage - 1e-9 || caught.jumped);
  assert.ok(player.currentTime >= targetBeforeOutage - 1e-9);
  player.stop();
});

test("consecutive failures past the cap emit a single termination event and stop", async () => {
  const { server, logger, player } = await harness({ maxConsecutiveFailures: 2 });
  const terminations = [];
  player.on("terminated", (payload) => terminations.push(payload));
  await player.start();

  // Every subsequent segment fails; cap (2) must be hit during recovery.
  server.enqueue("timeout", 20);
  const terminated = await once(player, "terminated");
  assert.equal(terminations.length, 1);
  assert.equal(terminated[0].attempts, 2);
  assert.equal(player.running, false);
  assert.equal(player.state, PlayerState.TERMINATED);

  const journaled = logger.recoveryRecords.filter((r) => r.event === "terminated");
  assert.equal(journaled.length, 1);
  assert.equal(journaled[0].reason, FailureReason.SEGMENT_TIMEOUT);

  const terminalTransition = logger.transitions.filter((t) => t.to === PlayerState.TERMINATED);
  assert.equal(terminalTransition.length, 1);
});
