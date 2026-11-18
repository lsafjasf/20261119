import { BoundedVideoFrameQueue } from "./frame-queue.js";
import { FrameConsumer } from "./frame-consumer.js";

const DEFAULT_NOW = () => globalThis.performance?.now?.() ?? Date.now();

export class DecodePipelineClosedError extends Error {
  constructor(message = "The decode pipeline is closed.") {
    super(message);
    this.name = "DecodePipelineClosedError";
  }
}

export class VideoDecodePipeline {
  constructor({
    createDecoder,
    maxOutputQueue = 4,
    maxDecodeQueue = 8,
    dropPolicy = "oldest-non-key",
    skipDiscardableChunks = true,
    now = DEFAULT_NOW,
    onError = null,
  } = {}) {
    if (typeof createDecoder !== "function") {
      throw new TypeError("createDecoder must be a function.");
    }
    if (!Number.isInteger(maxDecodeQueue) || maxDecodeQueue < 1) {
      throw new RangeError("maxDecodeQueue must be a positive integer.");
    }

    this._now = now;
    this._maxDecodeQueue = maxDecodeQueue;
    this._skipDiscardableChunks = skipDiscardableChunks;
    this._onError = onError;
    this._state = "open";
    this._closePromise = null;
    this._consumers = new Set();
    this._pendingChunkMetadata = new Map();

    this._submittedChunks = 0;
    this._skippedDiscardableChunks = 0;
    this._submittedDiscardableChunks = 0;
    this._decodedFrames = 0;
    this._decoderCloseCount = 0;
    this._decoderError = null;

    this.outputQueue = new BoundedVideoFrameQueue({
      capacity: maxOutputQueue,
      now,
      dropPolicy,
    });

    this.decoder = createDecoder({
      output: (frame) => this._onDecodedFrame(frame),
      error: (error) => this._onDecoderError(error),
    });
    if (!this.decoder || typeof this.decoder.decode !== "function") {
      throw new TypeError("createDecoder() must return a WebCodecs VideoDecoder-like object.");
    }
  }

  get state() {
    return this._state;
  }

  get decodeQueueSize() {
    return this.decoder.decodeQueueSize ?? 0;
  }

  configure(config) {
    this._assertOpen();
    this.decoder.configure(config);
    this._state = "configured";
  }

  _assertOpen() {
    if (this._state === "closed" || this._state === "closing") {
      throw new DecodePipelineClosedError();
    }
  }

  _onDecoderError(error) {
    this._decoderError = error;
    this._onError?.(error);
  }

  _onDecodedFrame(frame) {
    this._decodedFrames += 1;
    const metadata = this._pendingChunkMetadata.get(frame.timestamp) ?? {
      key: false,
      isReferenceFrame: true,
    };
    this._pendingChunkMetadata.delete(frame.timestamp);
    this.outputQueue.push(frame, {
      timestamp: frame.timestamp,
      key: metadata.key,
    });
  }

  submitEncodedChunk(chunk, { isReferenceFrame = true } = {}) {
    this._assertOpen();
    if (!chunk || typeof chunk.timestamp !== "number") {
      throw new TypeError("submitEncodedChunk() expects an EncodedVideoChunk-like object.");
    }

    const key = chunk.type === "key";
    const isReference = key || isReferenceFrame !== false;
    const decodeBacklogged = this.decodeQueueSize >= this._maxDecodeQueue;
    const maySkip =
      this._skipDiscardableChunks &&
      decodeBacklogged &&
      !key &&
      isReferenceFrame === false;

    if (maySkip) {
      this._skippedDiscardableChunks += 1;
      return { submitted: false, reason: "discardable-chunk-skipped" };
    }

    if (!isReference) this._submittedDiscardableChunks += 1;

    this._pendingChunkMetadata.set(chunk.timestamp, { key, isReferenceFrame: isReference });
    try {
      this.decoder.decode(chunk);
      this._submittedChunks += 1;
      return { submitted: true, reason: null };
    } catch (error) {
      this._pendingChunkMetadata.delete(chunk.timestamp);
      throw error;
    }
  }

  async flush() {
    this._assertOpen();
    await this.decoder.flush();
  }

  createConsumer(renderFrame, options = {}) {
    this._assertOpen();
    const consumer = new FrameConsumer(this.outputQueue, renderFrame, {
      now: this._now,
      ...options,
    });
    this._consumers.add(consumer);
    consumer.done.then(
      () => this._consumers.delete(consumer),
      () => this._consumers.delete(consumer),
    );
    return consumer;
  }

  snapshot() {
    const queueMetrics = this.outputQueue.snapshot();
    return {
      ...queueMetrics,
      decodeQueueSize: this.decodeQueueSize,
      submittedChunks: this._submittedChunks,
      submittedNonReferenceChunks: this._submittedDiscardableChunks,
      skippedCompressedFrames: this._skippedDiscardableChunks,
      skippedDiscardableChunks: this._skippedDiscardableChunks,
      skippedReferenceFrames: 0,
      decodedOutputFrames: this._decodedFrames,
      liveFrameCount: queueMetrics.outputQueueLength + queueMetrics.activeLeases,
      decoderCloseCount: this._decoderCloseCount,
      decoderError: this._decoderError,
      state: this._state,
    };
  }

  getMetrics() {
    return this.snapshot();
  }

  async close() {
    if (this._closePromise) return this._closePromise;

    this._closePromise = (async () => {
      this._state = "closing";
      const closeErrors = [];
      const consumers = [...this._consumers];
      const consumerStops = consumers.map((consumer) =>
        consumer.stop().catch((error) => closeErrors.push(error)),
      );

      try {
        this.decoder.close();
        this._decoderCloseCount += 1;
      } catch (error) {
        if (error?.name !== "InvalidStateError") closeErrors.push(error);
        else this._decoderCloseCount += 1;
      }

      await Promise.all(consumerStops);
      await this.outputQueue.close();
      this._pendingChunkMetadata.clear();
      this._state = "closed";

      if (closeErrors.length) {
        throw new AggregateError(closeErrors, "Decode pipeline close encountered errors.");
      }
      return this.snapshot();
    })();

    return this._closePromise;
  }
}
