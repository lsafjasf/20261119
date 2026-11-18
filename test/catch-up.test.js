import { test, after } from "node:test";
import assert from "node:assert/strict";
import { FaultInjectionServer } from "../src/fault/fault-injection-server.js";
import { SegmentClient } from "../src/player/segment-client.js";
import { SimulatedPlayer } from "../src/player/simulated-player.js";
import { TransitionLogger } from "../src/recovery/transition-logger.js";

test("long outage jumps to target segment without replaying from zero", async () => {
  const server = new FaultInjectionServer({
    segmentCount: 80,
    segmentDuration: 1,
    bitrates: [200, 500, 1000],
  });
  await server.listen();
  after(async () => server.close());

  const client = new SegmentClient({ baseUrl: server.baseUrl, requestTimeoutMs: 80 });
  const manifest = await client.getManifest();
  const logger = new TransitionLogger();
  const player = new SimulatedPlayer({
    client,
    logger,
    manifest,
    maxConsecutiveFailures: 10,
    backoffBaseMs: 2,
    tickIntervalMs: 10,
    tickStepSec: 0.05,
    catchUpWindowSec: 2,
  });

  await player.start();
  await new Promise((resolve) => {
    player.on("segment", (segment) => {
      if (segment.seq >= 2) resolve();
    });
  });
  const positionAtFailure = player.currentTime;

  // Block requests long enough for the virtual target to leave the catch-up
  // window, then restore the link.
  server.enqueue("reset", 10);
  await new Promise((resolve) => setTimeout(resolve, 700));
  server.recover();

  const caught = await new Promise((resolve) => {
    player.on("caught_up", resolve);
  });

  assert.equal(caught.jumped, true);
  assert.ok(caught.anchorTime >= positionAtFailure - 1e-9);
  assert.ok(caught.targetTime > caught.anchorTime + player.catchUpWindowSec);
  assert.ok(player.currentTime >= positionAtFailure - 1e-9, "play head never rewinds");

  const journalEntry = logger.recoveryRecords.find((r) => r.event === "catch_up");
  assert.equal(journalEntry.jumped, true);
  assert.equal(journalEntry.resumedFrom, journalEntry.anchorTime);
  player.stop();
});
