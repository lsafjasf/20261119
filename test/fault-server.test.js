import { test, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { FaultInjectionServer } from "../src/fault/fault-injection-server.js";
import { SegmentClient } from "../src/player/segment-client.js";
import {
  NetworkInterruptionError,
  SegmentTimeoutError,
} from "../src/recovery/errors.js";

async function setup(timeoutMs = 150) {
  const dir = await mkdtemp(join(tmpdir(), "fault-"));
  const server = new FaultInjectionServer({
    segmentCount: 8,
    timeoutDelayMs: 1000,
    logPath: join(dir, "faults.log"),
  });
  await server.listen();
  after(async () => {
    await server.close();
    await rm(dir, { recursive: true, force: true });
  });
  const client = new SegmentClient({
    baseUrl: server.baseUrl,
    requestTimeoutMs: timeoutMs,
  });
  return { server, client, dir };
}

test("healthy segments decode normally", async () => {
  const { client } = await setup();
  const manifest = await client.getManifest();
  assert.deepEqual(manifest.bitrates, [200, 500, 1000]);
  const segment = await client.getSegment(2, 1000);
  assert.equal(segment.seq, 2);
  assert.equal(segment.start, 2);
  assert.equal(segment.frames.length, 4);
});

test("timeout fault surfaces a typed SegmentTimeoutError", async () => {
  const { server, client } = await setup(120);
  server.enqueue("timeout", 1);
  await assert.rejects(client.getSegment(1, 1000), (error) => {
    assert.ok(error instanceof SegmentTimeoutError);
    assert.equal(error.seq, 1);
    return true;
  });
  // Fault is consumed once; the next request succeeds.
  const retried = await client.getSegment(1, 200);
  assert.equal(retried.seq, 1);
  assert.equal(server.counters.timeout, 1);
});

test("corrupt fault surfaces a decode error with wrong CRC", async () => {
  const { server, client } = await setup();
  server.enqueue("corrupt", 1);
  await assert.rejects(client.getSegment(4, 1000), (error) => {
    assert.ok(error instanceof Error);
    assert.match(error.message, /crc mismatch/);
    assert.ok(error.name !== "SegmentTimeoutError");
    return true;
  });
  assert.equal(server.counters.corrupt, 1);
});

test("reset fault surfaces a network interruption, then recover clears queue", async () => {
  const { server, client } = await setup();
  server.enqueue("reset", 2);
  await assert.rejects(client.getSegment(5, 1000), (error) => {
    assert.ok(error instanceof NetworkInterruptionError);
    return true;
  });
  assert.equal(server.status().queue.length, 1);
  const cleared = server.recover();
  assert.equal(cleared, 1);
  const segment = await client.getSegment(5, 500);
  assert.equal(segment.seq, 5);
  assert.equal(server.counters.reset, 1);
});

test("faults can be driven through the HTTP control API", async () => {
  const { server, client } = await setup(120);
  const base = server.baseUrl;
  const post = (path, body) =>
    fetch(`${base}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then((res) => res.json());

  assert.deepEqual(await post("/faults/corrupt", { count: 1 }), {
    ok: true,
    type: "corrupt",
    queued: 1,
  });
  await assert.rejects(client.getSegment(0, 1000), /crc mismatch/);

  await post("/faults/reset", { count: 2 });
  const status = await (await fetch(`${base}/faults/status`)).json();
  assert.equal(status.queue.length, 2);
  const recovered = await post("/faults/recover", {});
  assert.equal(recovered.cleared, 2);
});
