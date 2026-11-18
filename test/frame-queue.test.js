import assert from "node:assert/strict";
import test from "node:test";
import { BoundedVideoFrameQueue } from "../src/frame-queue.js";
import { FakeVideoFrame } from "./helpers.js";

test("queue bounds output and closes evicted frames", () => {
  let now = 0;
  const queue = new BoundedVideoFrameQueue({
    capacity: 2,
    now: () => now,
  });
  const frames = Array.from({ length: 6 }, (_, index) => new FakeVideoFrame(index));

  for (const frame of frames) queue.push(frame, { timestamp: frame.timestamp });

  const metrics = queue.snapshot();
  assert.equal(queue.length, 2);
  assert.equal(metrics.peakOutputQueueLength, 2);
  assert.equal(metrics.droppedDecodedFrames, 4);
  assert.equal(metrics.frameCloseCount, 4);
  assert.equal(frames.slice(0, 4).every((frame) => frame.closed), true);
  assert.equal(frames.slice(4).every((frame) => !frame.closed), true);
});

test("drop policy prefers old non-key output, but remains bounded for all-key output", () => {
  const queue = new BoundedVideoFrameQueue({ capacity: 3, now: () => 0 });
  const key = new FakeVideoFrame(0);
  const firstDelta = new FakeVideoFrame(1);
  const secondDelta = new FakeVideoFrame(2);
  const nextDelta = new FakeVideoFrame(3);

  queue.push(key, { key: true });
  queue.push(firstDelta, { key: false });
  queue.push(secondDelta, { key: false });
  queue.push(nextDelta, { key: false });

  assert.equal(key.closed, false);
  assert.equal(firstDelta.closed, true);
  assert.deepEqual(
    queue.snapshot().droppedDecodedFrames,
    1,
  );

  queue.push(new FakeVideoFrame(4), { key: true });
  queue.push(new FakeVideoFrame(5), { key: true });
  assert.equal(queue.length, 3);
  assert.equal(queue.snapshot().peakOutputQueueLength, 3);
});

test("leases track queue wait and close exactly once", async () => {
  let now = 10;
  const queue = new BoundedVideoFrameQueue({ capacity: 2, now: () => now });
  const frame = new FakeVideoFrame(42);
  queue.push(frame, { timestamp: 42, key: false });

  now = 35;
  const lease = await queue.take();
  assert.equal(lease.waitMs, 25);
  assert.equal(queue.length, 0);
  assert.equal(queue.snapshot().activeLeases, 1);

  lease.release();
  lease.release();
  assert.equal(frame.closed, true);
  assert.equal(frame.closeCount, 1);
  assert.equal(queue.snapshot().frameCloseCount, 1);
  assert.equal(queue.snapshot().averageWaitMs, 25);
});

test("close drains queued frames and resolves waiting consumers", async () => {
  const queue = new BoundedVideoFrameQueue({ capacity: 3, now: () => 0 });
  const first = new FakeVideoFrame(1);
  const second = new FakeVideoFrame(2);
  queue.push(first);
  queue.push(second);

  await queue.close();
  assert.equal(first.closed, true);
  assert.equal(second.closed, true);
  assert.equal(queue.snapshot().frameCloseCount, 2);

  const emptyQueue = new BoundedVideoFrameQueue({ capacity: 1, now: () => 0 });
  const waiting = emptyQueue.take();
  await emptyQueue.close();
  assert.equal(await waiting, null);

  const late = new FakeVideoFrame(3);
  const result = queue.push(late);
  assert.equal(result.accepted, false);
  assert.equal(late.closed, true);
  assert.equal(queue.snapshot().lateFramesAfterClose, 1);
});

test("close waits for an active lease before completing", async () => {
  const queue = new BoundedVideoFrameQueue({ capacity: 1, now: () => 0 });
  const frame = new FakeVideoFrame(1);
  queue.push(frame);
  const lease = await queue.take();

  let closed = false;
  const closePromise = queue.close().then(() => {
    closed = true;
  });
  await Promise.resolve();
  assert.equal(closed, false);
  assert.equal(frame.closed, false);

  lease.release();
  await closePromise;
  assert.equal(closed, true);
  assert.equal(frame.closed, true);
});
