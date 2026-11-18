export class FakeVideoFrame {
  constructor(timestamp = 0) {
    this.timestamp = timestamp;
    this.closeCount = 0;
    this.closed = false;
  }

  close() {
    if (!this.closed) {
      this.closed = true;
      this.closeCount += 1;
    }
  }
}

export class FakeVideoDecoder {
  constructor({ output, error }) {
    this.output = output;
    this.error = error;
    this.decodeQueueSize = 0;
    this.decodedChunks = [];
    this.closeCount = 0;
    this.closed = false;
    this.outputsAfterClose = [];
  }

  configure(config) {
    this.config = config;
  }

  decode(chunk) {
    if (this.closed) throw new DOMException("Decoder is closed.", "InvalidStateError");
    this.decodedChunks.push(chunk);
    this.decodeQueueSize += 1;
  }

  completeDecode(timestamp = this.decodedChunks.at(-1)?.timestamp ?? 0) {
    this.decodeQueueSize = Math.max(0, this.decodeQueueSize - 1);
    return this.emit(timestamp);
  }

  emit(timestamp, frame = new FakeVideoFrame(timestamp)) {
    if (this.closed) this.outputsAfterClose.push(frame);
    this.output(frame);
    return frame;
  }

  async flush() {
    this.decodeQueueSize = 0;
  }

  close() {
    this.closeCount += 1;
    this.closed = true;
    this.decodeQueueSize = 0;
  }
}

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function encodedChunk(timestamp, type = "delta") {
  return { timestamp, type };
}
