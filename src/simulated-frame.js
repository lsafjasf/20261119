export class FrameClosedError extends Error {
  constructor(message = "VideoFrame is closed") {
    super(message);
    this.name = "FrameClosedError";
  }
}

export class SimulatedVideoFrame {
  #closed = false;

  constructor({ width = 1920, height = 1080, timestamp = 0, label = "frame" } = {}) {
    this.width = width;
    this.height = height;
    this.timestamp = timestamp;
    this.label = label;
    this.closeCalls = 0;
    this.objectMarker = `object:${label}:${timestamp}`;
  }

  get closed() {
    return this.#closed;
  }

  clone() {
    if (this.#closed) {
      throw new FrameClosedError(`Cannot clone closed frame ${this.label}`);
    }
    return new SimulatedVideoFrame({
      width: this.width,
      height: this.height,
      timestamp: this.timestamp,
      label: `${this.label}:clone`,
    });
  }

  use() {
    if (this.#closed) {
      throw new FrameClosedError(`Cannot use closed frame ${this.label}`);
    }
    return { width: this.width, height: this.height, timestamp: this.timestamp };
  }

  close() {
    if (this.#closed) {
      throw new FrameClosedError(`Frame ${this.label} was already closed`);
    }
    this.closeCalls += 1;
    this.#closed = true;
  }
}

export function createMonotonicClock(start = Date.now()) {
  let tick = 0;
  return () => {
    const at = new Date(start + tick * 25).toISOString();
    tick += 1;
    return at;
  };
}
