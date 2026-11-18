import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { TransitionLogger } from "../src/recovery/transition-logger.js";

test("state transitions and recovery records are persisted as JSONL", async () => {
  const dir = await mkdtemp(join(tmpdir(), "journal-"));
  const transitionsPath = join(dir, "transitions.log");
  const recoveryPath = join(dir, "recovery.log");
  const logger = new TransitionLogger({ transitionsPath, recoveryPath });

  logger.logTransition({ from: "PLAYING", to: "RECOVERING", reason: "segment_timeout" });
  logger.logRecovery({ event: "recovery", attempt: 1, result: "failed", bitrate: 500 });
  logger.logTermination({ reason: "segment_timeout", attempts: 3 });

  const transitions = (await readFile(transitionsPath, "utf8")).trim().split("\n").map(JSON.parse);
  const recovery = (await readFile(recoveryPath, "utf8")).trim().split("\n").map(JSON.parse);

  assert.equal(transitions[0].to, "RECOVERING");
  assert.equal(recovery[0].bitrate, 500);
  assert.deepEqual(recovery.map((r) => r.event), ["recovery", "terminated"]);

  await rm(dir, { recursive: true, force: true });
});

test("termination can only be journaled once", () => {
  const logger = new TransitionLogger();
  logger.logTermination({ reason: "decode_error", attempts: 2 });
  assert.throws(() => logger.logTermination({ reason: "decode_error", attempts: 2 }), /already emitted/);
  assert.equal(logger.recoveryRecords.filter((r) => r.event === "terminated").length, 1);
});
