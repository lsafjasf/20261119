const DEFAULT_NOW = () => globalThis.performance?.now?.() ?? Date.now();

function abortError(signal) {
  return signal?.reason instanceof Error
    ? signal.reason
    : new DOMException("The operation was aborted.", "AbortError");
}

function delay(ms, signal) {
  if (signal?.aborted) return Promise.reject(abortError(signal));
  if (!(ms > 0)) return Promise.resolve();

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortError(signal));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export class FrameConsumer {
  constructor(queue, renderFrame, { delayMs = 0, now = DEFAULT_NOW } = {}) {
    if (!queue || typeof queue.take !== "function") {
      throw new TypeError("FrameConsumer requires a frame queue.");
    }
    if (typeof renderFrame !== "function") {
      throw new TypeError("FrameConsumer requires a renderFrame function.");
    }
    if (!Number.isFinite(delayMs) || delayMs < 0) {
      throw new RangeError("delayMs must be a non-negative number.");
    }

    this.queue = queue;
    this.renderFrame = renderFrame;
    this._delayMs = delayMs;
    this._now = now;
    this._status = "idle";
    this._running = false;
    this._abortController = null;
    this._loopPromise = null;
    this._stopPromise = null;
    this._renderedFrames = 0;
    this._renderErrors = 0;
    this._lastRenderError = null;
    this._startedAt = null;
    this._stoppedAt = null;

    this.done = new Promise((resolve) => {
      this._resolveDone = resolve;
    });
  }

  get running() {
    return this._running;
  }

  get delayMs() {
    return this._delayMs;
  }

  setDelayMs(delayMs) {
    if (!Number.isFinite(delayMs) || delayMs < 0) {
      throw new RangeError("delayMs must be a non-negative number.");
    }
    this._delayMs = delayMs;
  }

  start() {
    if (this._running) return;
    if (this._status === "stopped") {
      throw new Error("A stopped FrameConsumer cannot be restarted.");
    }
    this._status = "running";
    this._running = true;
    this._startedAt = this._now();
    this._abortController = new AbortController();
    this._loopPromise = this._run(this._abortController.signal);
  }

  async _run(signal) {
    try {
      while (this._running) {
        let lease;
        try {
          lease = await this.queue.take({ signal });
        } catch (error) {
          if (signal.aborted) break;
          throw error;
        }
        if (!lease) break;

        try {
          await this.renderFrame(lease.frame, {
            lease,
            timestamp: lease.timestamp,
            key: lease.key,
            waitMs: lease.waitMs,
            signal,
          });
          if (!signal.aborted) this._renderedFrames += 1;
        } catch (error) {
          if (!signal.aborted) {
            this._renderErrors += 1;
            this._lastRenderError = error;
          }
        } finally {
          lease.release();
        }

        if (!this._running) break;
        try {
          await delay(this._delayMs, signal);
        } catch (error) {
          if (!signal.aborted) throw error;
          break;
        }
      }
    } finally {
      this._status = "stopped";
      this._running = false;
      this._stoppedAt = this._now();
      this._resolveDone(this.snapshot());
    }
  }

  async stop() {
    if (this._stopPromise) return this._stopPromise;
    if (this._status === "idle") {
      this._status = "stopped";
      this._stoppedAt = this._now();
      this._resolveDone(this.snapshot());
      this._stopPromise = Promise.resolve(this.snapshot());
      return this._stopPromise;
    }
    this._running = false;
    this._abortController?.abort();
    this._stopPromise = (async () => {
      await this._loopPromise;
      return this.snapshot();
    })();
    return this._stopPromise;
  }

  snapshot() {
    return {
      running: this._running,
      delayMs: this._delayMs,
      renderedFrames: this._renderedFrames,
      renderErrors: this._renderErrors,
      lastRenderError: this._lastRenderError,
      startedAt: this._startedAt,
      stoppedAt: this._stoppedAt,
    };
  }
}
