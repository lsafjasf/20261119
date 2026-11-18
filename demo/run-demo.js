import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { FaultInjectionServer } from "../src/fault/fault-injection-server.js";
import { SegmentClient } from "../src/player/segment-client.js";
import { SimulatedPlayer } from "../src/player/simulated-player.js";
import { TransitionLogger } from "../src/recovery/transition-logger.js";
import { FailureReason } from "../src/recovery/errors.js";

const LOG_DIR = join(process.cwd(), "logs");
mkdirSync(LOG_DIR, { recursive: true });

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function buildPlayer(label, { maxConsecutiveFailures = 3 } = {}) {
  const server = new FaultInjectionServer({
    segmentCount: 80,
    segmentDuration: 1,
    bitrates: [200, 500, 1000],
    timeoutDelayMs: 400,
    logPath: join(LOG_DIR, `${label}-faults.log`),
  });
  await server.listen();
  const client = new SegmentClient({ baseUrl: server.baseUrl, requestTimeoutMs: 120 });
  const manifest = await client.getManifest();
  const logger = new TransitionLogger({
    transitionsPath: join(LOG_DIR, `${label}-transitions.log`),
    recoveryPath: join(LOG_DIR, `${label}-recovery.log`),
  });
  const player = new SimulatedPlayer({
    client,
    logger,
    manifest,
    maxConsecutiveFailures,
    backoffBaseMs: 20,
    tickIntervalMs: 10,
    tickStepSec: 0.05,
  });
  return { server, player, logger };
}

async function scenario(label, inject, options = {}) {
  console.log(`\n=== ${label} ===`);
  const { server, player, logger } = await buildPlayer(label, options);
  const events = [];
  for (const name of ["failure", "recovered", "caught_up", "terminated"]) {
    player.on(name, (payload) => events.push({ name, payload }));
  }
  await player.start();
  await sleep(200);
  await inject(server, player);
  await sleep(900);
  player.stop();
  await server.close();
  for (const event of events) {
    console.log(`  ${event.name}:`, JSON.stringify(event.payload));
  }
  return logger;
}

async function main() {
  await scenario("timeout", (server) => server.enqueue("timeout", 1));

  await scenario("corrupt", (server) => server.enqueue("corrupt", 1));

  await scenario(
    "network",
    async (server) => {
      server.enqueue("reset", 6);
      await sleep(150);
      server.recover();
    },
    { maxConsecutiveFailures: 8 },
  );

  await scenario(
    "terminate",
    (server) => server.enqueue("timeout", 20),
    { maxConsecutiveFailures: 3 },
  );

  const summary = JSON.parse(
    readFileSync(join(LOG_DIR, "terminate-recovery.log"), "utf8")
      .trim()
      .split("\n")
      .pop(),
  );
  console.log("\ntermination journal entry:", summary);
  console.log(`\nlogs written under ${LOG_DIR}/`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
