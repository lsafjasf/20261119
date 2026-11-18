import assert from "node:assert/strict";
import test from "node:test";
import { VideoDecodePipeline } from "../src/decode-pipeline.js";
import {
  FakeVideoDecoder,
  FakeVideoFrame,
  encodedChunk,
  sleep,
} from "./helpers.js";

function createPipeline(options = {}) {
  let decoder;
  const pipeline = new VideoDecodePipeline({
    createDecoder(callbacks) {
      decoder = new FakeVideoDecoder(callbacks);
      return decoder;
    },
    maxOutputQueue: 3,
    maxDecodeQueue: 4,
    ...options,
  });
  return { pipeline, getDecoder: () => decoder };
}

test("bounded output queue records peak, drops prefixed frames and keeps newest output", () => {
  const { pipeline, getDecoder } = createPipeline({ maxOutputQueue: 2 });
  const decoder = getDecoder();
  const frames = [0, 1, 2, 3].map((timestamp) => decoder.emit(timestamp));

  const metrics = pipeline.snapshot();
  assert.equal(metrics.outputQueueLength, 2);
  assert.equal(metrics.peakOutputQueueLength, 2);
  assert.equal(metrics.droppedDecodedFrames, 2);
  assert.equal(metrics.liveFrameCount, 2);
  assert.equal(frames[0].closed, true);
  assert.equal(frames[1].closed, true);
  assert.equal(frames[2].closed, false);
  assert.equal(frames[3].closed, false);
});

test("compressed skipping only removes explicit non-reference chunks", () => {
  const { pipeline, getDecoder } = createPipeline({ maxDecodeQueue: 2 });
  const decoder = getDecoder();
  decoder.decodeQueueSize = 2;

  const chunks = [
    encodedChunk(0, "key"),
    encodedChunk(1, "delta"),
    encodedChunk(2, "delta"),
    encodedChunk(3, "delta"),
    encodedChunk(4, "key"),
    encodedChunk(5, "delta"),
  ];
  const results = [
    pipeline.submitEncodedChunk(chunks[0], { isReferenceFrame: false }),
    pipeline.submitEncodedChunk(chunks[1]),
    pipeline.submitEncodedChunk(chunks[2], { isReferenceFrame: false }),
    pipeline.submitEncodedChunk(chunks[3], { isReferenceFrame: false }),
    pipeline.submitEncodedChunk(chunks[4], { isReferenceFrame: false }),
    pipeline.submitEncodedChunk(chunks[5]),
  ];

  assert.deepEqual(
    decoder.decodedChunks.map((chunk) => chunk.timestamp),
    [0, 1, 4, 5],
  );
  assert.deepEqual(
    results.map((result) => result.submitted),
    [true, true, false, false, true, true],
  );
  assert.equal(pipeline.snapshot().skippedCompressedFrames, 2);
  assert.equal(pipeline.snapshot().skippedReferenceFrames, 0);
  assert.equal(pipeline.snapshot().submittedNonReferenceChunks, 0);
});

test("discardable compressed chunks are decoded when skipping is disabled", () => {
  const { pipeline, getDecoder } = createPipeline({
    maxDecodeQueue: 1,
    skipDiscardableChunks: false,
  });
  const decoder = getDecoder();
  decoder.decodeQueueSize = 99;

  const result = pipeline.submitEncodedChunk(encodedChunk(10), {
    isReferenceFrame: false,
  });
  assert.equal(result.submitted, true);
  assert.equal(decoder.decodedChunks.length, 1);
  assert.equal(pipeline.snapshot().skippedCompressedFrames, 0);
});

test("average wait measures time from decode output to consumer lease", async () => {
  let now = 100;
  const { pipeline, getDecoder } = createPipeline({
    maxOutputQueue: 2,
    now: () => now,
  });
  getDecoder().emit(1);
  now = 112;
  const lease = await pipeline.outputQueue.take();

  assert.equal(lease.waitMs, 12);
  assert.equal(pipeline.snapshot().averageWaitMs, 12);
  lease.release();
});

test("close drains queued frames, closes decoder once and reclaims late output", async () => {
  const { pipeline, getDecoder } = createPipeline({ maxOutputQueue: 4 });
  const decoder = getDecoder();
  const frames = [0, 1, 2].map((timestamp) => decoder.emit(timestamp));

  const [metrics, secondClose] = await Promise.all([
    pipeline.close(),
    pipeline.close(),
  ]);
  assert.equal(decoder.closeCount, 1);
  assert.equal(secondClose.decoderCloseCount, 1);
  assert.equal(metrics.decoderCloseCount, 1);
  assert.equal(metrics.state, "closed");
  assert.equal(metrics.liveFrameCount, 0);
  assert.equal(metrics.frameCloseCount, 3);
  assert.equal(frames.every((frame) => frame.closed), true);

  const late = decoder.emit(99);
  assert.equal(late.closed, true);
  assert.equal(pipeline.snapshot().lateFramesAfterClose, 1);
});

test("slow consumer keeps queue bounded and close reclaims the active frame", async () => {
  const { pipeline, getDecoder } = createPipeline({
    maxOutputQueue: 4,
    maxDecodeQueue: 8,
  });
  const decoder = getDecoder();
  let generated = 0;
  let renderStarted = false;
  let finishRender;
  const renderGate = new Promise((resolve) => {
    finishRender = resolve;
  });
  const consumer = pipeline.createConsumer(async (frame) => {
    if (!renderStarted) {
      renderStarted = true;
      await renderGate;
    }
  });
  consumer.start();

  const producer = setInterval(() => {
    decoder.emit(new FakeVideoFrame(generated));
    generated += 1;
  }, 1);
  await sleep(45);
  clearInterval(producer);

  const pressured = pipeline.snapshot();
  assert.equal(renderStarted, true);
  assert.ok(pressured.peakOutputQueueLength <= 4);
  assert.ok(pressured.droppedDecodedFrames > 0);
  assert.ok(pressured.averageWaitMs >= 0);
  assert.ok(pressured.outputQueueLength <= 4);

  let closeFinished = false;
  const closePromise = pipeline.close().then((metrics) => {
    closeFinished = true;
    return metrics;
  });
  await sleep(5);
  assert.equal(closeFinished, false);
  finishRender();

  const closed = await closePromise;
  assert.equal(closed.decoderCloseCount, 1);
  assert.equal(closed.liveFrameCount, 0);
  assert.equal(closed.state, "closed");
  assert.equal(closed.frameCloseCount, generated);
  assert.equal(consumer.running, false);
});
