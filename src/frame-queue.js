const DEFAULT_NOW = () => globalThis.performance?.now?.() ?? Date.now();

function closeFrame(frame) {
  try {
    frame.close();
  } catch {
    // A frame may already have been closed by the caller. The registry still
    // drops its reference so it cannot leak through this queue.
  }
}

function abortError(signal) {
  return signal?.reason instanceof Error
    ? signal.reason
    : new DOMException("The operation was aborted.", "AbortError");
}

export class BoundedVideoFrameQueue {
  constructor({
    capacity,
    now = DEFAULT_NOW,
    dropPolicy = "oldest-non-key",
  } = {}) {
    if (!Number.isInteger(capacity) || capacity < 1) {
      throw new RangeError("capacity must be a positive integer.");
    }
    if (!["oldest-non-key", "newest-non-key", "oldest", "newest"].includes(dropPolicy)) {
      throw new RangeError(`Unknown drop policy: ${dropPolicy}`);
    }

    this.capacity = capacity;
    this.dropPolicy = dropPolicy;
    this._now = now;
    this._entries = [];
    this._waiters = new Set();
    this._leases = new Set();
    this._closed = false;
    this._releaseWaiters = new Set();
    this._nextId = 1;

    this._peakLength = 0;
    this._lengthSamples = 0;
    this._lengthTotal = 0;
    this._waitSamples = 0;
    this._waitTotalMs = 0;
    this._droppedFrames = 0;
    this._frameCloseCount = 0;
    this._lateFramesAfterClose = 0;
    this._dequeuedFrames = 0;
  }

  get length() {
    return this._entries.length;
  }

  get closed() {
    return this._closed;
  }

  _sampleLength() {
    this._lengthSamples += 1;
    this._lengthTotal += this._entries.length;
    this._peakLength = Math.max(this._peakLength, this._entries.length);
  }

  _closeEntry(entry) {
    closeFrame(entry.frame);
    this._frameCloseCount += 1;
  }

  _chooseDropIndex() {
    const { dropPolicy } = this;
    if (dropPolicy === "oldest") return 0;
    if (dropPolicy === "newest") return this._entries.length - 1;

    const indexes =
      dropPolicy === "oldest-non-key"
        ? this._entries.keys()
        : [...this._entries.keys()].reverse();
    for (const index of indexes) {
      if (!this._entries[index].key) return index;
    }
    return dropPolicy === "oldest-non-key" ? 0 : this._entries.length - 1;
  }

  push(frame, metadata = {}) {
    if (!frame || typeof frame.close !== "function") {
      throw new TypeError("push() expects a VideoFrame-like object with close().");
    }

    if (this._closed) {
      closeFrame(frame);
      this._frameCloseCount += 1;
      this._lateFramesAfterClose += 1;
      return { accepted: false, dropped: null, reason: "closed" };
    }

    let dropped = null;
    if (this._entries.length >= this.capacity) {
      const index = this._chooseDropIndex();
      const [victim] = this._entries.splice(index, 1);
      this._closeEntry(victim);
      this._droppedFrames += 1;
      dropped = {
        id: victim.id,
        timestamp: victim.timestamp,
        key: victim.key,
      };
    }

    const entry = {
      id: this._nextId,
      frame,
      timestamp: metadata.timestamp ?? frame.timestamp,
      key: Boolean(metadata.key),
      enqueuedAt: this._now(),
    };
    this._nextId += 1;
    this._entries.push(entry);
    this._sampleLength();
    this._flushWaiters();

    return { accepted: true, dropped, reason: null };
  }

  _makeLease(entry) {
    const dequeuedAt = this._now();
    const waitMs = Math.max(0, dequeuedAt - entry.enqueuedAt);
    this._waitSamples += 1;
    this._waitTotalMs += waitMs;
    this._dequeuedFrames += 1;

    const lease = {
      id: entry.id,
      frame: entry.frame,
      key: entry.key,
      timestamp: entry.timestamp,
      enqueuedAt: entry.enqueuedAt,
      dequeuedAt,
      waitMs,
      released: false,
      release: () => this.release(lease),
    };
    this._leases.add(lease);
    this._sampleLength();
    return lease;
  }

  _flushWaiters() {
    if (!this._entries.length) return;
    for (const waiter of [...this._waiters]) {
      if (!this._entries.length) break;
      this._waiters.delete(waiter);
      waiter.cleanup();
      const entry = this._entries.shift();
      waiter.resolve(this._makeLease(entry));
    }
  }

  async take({ signal } = {}) {
    if (signal?.aborted) throw abortError(signal);

    if (this._entries.length) {
      return this._makeLease(this._entries.shift());
    }
    if (this._closed) return null;

    return new Promise((resolve, reject) => {
      const waiter = {
        resolve,
        reject,
        cleanup: () => {
          signal?.removeEventListener("abort", onAbort);
        },
      };
      const onAbort = () => {
        this._waiters.delete(waiter);
        waiter.cleanup();
        reject(abortError(signal));
      };
      this._waiters.add(waiter);
      signal?.addEventListener("abort", onAbort, { once: true });
    });
  }

  release(lease) {
    if (!lease || lease.released) return;
    lease.released = true;
    this._leases.delete(lease);
    closeFrame(lease.frame);
    this._frameCloseCount += 1;
    this._wakeReleaseWaiters();
  }

  _wakeReleaseWaiters() {
    if (!this._closed || this._leases.size) return;
    for (const resolve of this._releaseWaiters) resolve();
    this._releaseWaiters.clear();
  }

  async close() {
    if (!this._closed) {
      this._closed = true;
      for (const waiter of this._waiters) {
        waiter.cleanup();
        waiter.resolve(null);
      }
      this._waiters.clear();
      for (const entry of this._entries) this._closeEntry(entry);
      this._entries.length = 0;
      this._sampleLength();
    }

    if (!this._leases.size) return;
    await new Promise((resolve) => this._releaseWaiters.add(resolve));
  }

  snapshot() {
    return {
      outputQueueLength: this._entries.length,
      peakOutputQueueLength: this._peakLength,
      averageOutputQueueLength:
        this._lengthSamples === 0 ? 0 : this._lengthTotal / this._lengthSamples,
      averageWaitMs: this._waitSamples === 0 ? 0 : this._waitTotalMs / this._waitSamples,
      droppedDecodedFrames: this._droppedFrames,
      dequeuedFrames: this._dequeuedFrames,
      activeLeases: this._leases.size,
      frameCloseCount: this._frameCloseCount,
      lateFramesAfterClose: this._lateFramesAfterClose,
    };
  }

  getMetrics() {
    return this.snapshot();
  }
}
