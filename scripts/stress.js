import { VideoDecodePipeline } from "../src/decode-pipeline.js";

class SyntheticFrame {
  constructor(timestamp) {
    this.timestamp = timestamp;
    this.closed = false;
  }

  close() {
    this.closed = true;
  }
}

class SyntheticDecoder {
  constructor({ output, error }) {
    this.output = output;
    this.error = error;
    this.decodeQueueSize = 0;
    this.closeCount = 0;
    this.frames = [];
    this.decodedChunks = [];
  }

  configure(config) {
    this.config = config;
  }

  decode(chunk) {
    this.decodedChunks.push(chunk);
    this.decodeQueueSize += 1;
  }

  flush() {
    this.decodeQueueSize = 0;
  }

  emit(timestamp) {
    const frame = new SyntheticFrame(timestamp);
    this.frames.push(frame);
    this.output(frame);
  }

  close() {
    this.closeCount += 1;
    this.decodeQueueSize = 0;
  }
}

function positiveNumber(value, fallback, name) {
  if (value === undefined) return fallback;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new RangeError(`${name} must be a positive number.`);
  }
  return parsed;
}

function parseArgs(argv) {
  const values = {};
  for (const argument of argv) {
    const match = /^--([^=]+)=(.+)$/.exec(argument);
    if (match) values[match[1]] = match[2];
  }
  return {
    outputCapacity: Math.max(
      1,
      Math.floor(positiveNumber(values.queue, 6, "--queue")),
    ),
    maxDecodeQueue: Math.max(
      1,
      Math.floor(positiveNumber(values["decode-queue"], 8, "--decode-queue")),
    ),
    producerIntervalMs: positiveNumber(
      values["producer-interval"],
      1,
      "--producer-interval",
    ),
    renderDelayMs: positiveNumber(
      values["render-delay"],
      12,
      "--render-delay",
    ),
    pressureDurationMs: positiveNumber(
      values.duration,
      350,
      "--duration",
    ),
  };
}

const config = parseArgs(process.argv.slice(2));

let decoder;
const pipeline = new VideoDecodePipeline({
  createDecoder(callbacks) {
    decoder = new SyntheticDecoder(callbacks);
    return decoder;
  },
  maxOutputQueue: config.outputCapacity,
  maxDecodeQueue: config.maxDecodeQueue,
});

const consumer = pipeline.createConsumer(async () => {
  await new Promise((resolve) => setTimeout(resolve, config.renderDelayMs));
});
consumer.start();

let produced = 0;
const producer = setInterval(() => {
  decoder.emit(produced);
  produced += 1;
}, config.producerIntervalMs);

await new Promise((resolve) => setTimeout(resolve, config.pressureDurationMs));
clearInterval(producer);

const pressure = pipeline.snapshot();
const closeMetrics = await pipeline.close();
const stillOpen = decoder.frames.filter((frame) => !frame.closed);

async function runDependencyProbe() {
  let probeDecoder;
  const probe = new VideoDecodePipeline({
    createDecoder(callbacks) {
      probeDecoder = new SyntheticDecoder(callbacks);
      probeDecoder.decodeQueueSize = 1;
      return probeDecoder;
    },
    maxOutputQueue: 1,
    maxDecodeQueue: 1,
  });
  const chunks = [
    { timestamp: 0, type: "key" },
    { timestamp: 1, type: "delta" },
    { timestamp: 2, type: "delta" },
    { timestamp: 3, type: "delta" },
    { timestamp: 4, type: "key" },
  ];
  const decisions = [
    probe.submitEncodedChunk(chunks[0], { isReferenceFrame: false }),
    probe.submitEncodedChunk(chunks[1]),
    probe.submitEncodedChunk(chunks[2], { isReferenceFrame: false }),
    probe.submitEncodedChunk(chunks[3]),
    probe.submitEncodedChunk(chunks[4], { isReferenceFrame: false }),
  ];
  const metrics = probe.snapshot();
  const probeClose = await probe.close();
  return {
    submittedReferenceTimestamps: [0, 1, 3, 4],
    decodedTimestamps: probeDecoder.decodedChunks.map((chunk) => chunk.timestamp),
    submitted: decisions.map((decision) => decision.submitted),
    skippedCompressedFrames: metrics.skippedCompressedFrames,
    skippedReferenceFrames: metrics.skippedReferenceFrames,
    decoderCloseCount: probeClose.decoderCloseCount,
  };
}
const dependencySafety = await runDependencyProbe();

const report = {
  config: {
    maxOutputQueue: config.outputCapacity,
    maxDecodeQueue: config.maxDecodeQueue,
    producerIntervalMs: config.producerIntervalMs,
    renderDelayMs: config.renderDelayMs,
    pressureDurationMs: config.pressureDurationMs,
  },
  pressure: {
    producedFrames: produced,
    renderedFrames: consumer.snapshot().renderedFrames,
    currentOutputQueueLength: pressure.outputQueueLength,
    peakOutputQueueLength: pressure.peakOutputQueueLength,
    averageOutputQueueLength: Number(pressure.averageOutputQueueLength.toFixed(2)),
    averageWaitMs: Number(pressure.averageWaitMs.toFixed(2)),
    droppedDecodedFrames: pressure.droppedDecodedFrames,
    skippedCompressedFrames: pressure.skippedCompressedFrames,
    skippedReferenceFrames: pressure.skippedReferenceFrames,
  },
  dependencySafety,
  shutdown: {
    decoderCloseCount: closeMetrics.decoderCloseCount,
    frameCloseCount: closeMetrics.frameCloseCount,
    liveFrameCount: closeMetrics.liveFrameCount,
    openFramesAfterClose: stillOpen.length,
  },
};

console.log(JSON.stringify(report, null, 2));

if (
  pressure.peakOutputQueueLength > config.outputCapacity ||
  pressure.droppedDecodedFrames === 0 ||
  pressure.skippedReferenceFrames !== 0 ||
  report.dependencySafety.skippedReferenceFrames !== 0 ||
  report.dependencySafety.skippedCompressedFrames !== 1 ||
  closeMetrics.decoderCloseCount !== 1 ||
  closeMetrics.liveFrameCount !== 0 ||
  stillOpen.length !== 0 ||
  closeMetrics.frameCloseCount !== produced
) {
  process.exitCode = 1;
}
