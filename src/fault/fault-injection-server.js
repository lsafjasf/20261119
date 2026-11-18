import { createServer } from "node:http";
import { appendFileSync } from "node:fs";
import { encodeSegment, HEADER_SIZE } from "../recovery/protocol.js";

function readJson(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      if (chunks.length === 0) return resolve({});
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
      } catch (error) {
        reject(error);
      }
    });
    req.on("error", reject);
  });
}

// Local fault injection service. Each queued fault is consumed by exactly one
// segment request, so scenarios are deterministic:
//   timeout : hold the response past the client timeout
//   corrupt : return a well-framed segment whose payload CRC is wrong
//   reset   : destroy the socket (connection interruption)
//   recover : clear the queue, emulating the link coming back
export class FaultInjectionServer {
  constructor({
    segmentCount = 40,
    segmentDuration = 1,
    bitrates = [200, 500, 1000],
    timeoutDelayMs = 3000,
    logPath = null,
  } = {}) {
    this.manifest = { segmentCount, segmentDuration, bitrates };
    this.timeoutDelayMs = timeoutDelayMs;
    this.logPath = logPath;
    this.queue = [];
    this.counters = { timeout: 0, corrupt: 0, reset: 0, served: 0 };
    this.pendingTimers = new Set();
    this.server = createServer((req, res) => this._route(req, res));
  }

  listen(port = 0, host = "127.0.0.1") {
    return new Promise((resolve) => {
      this.server.listen(port, host, () => resolve(this.server.address()));
    });
  }

  close() {
    return new Promise((resolve, reject) => {
      this.server.close((error) => {
        for (const timer of this.pendingTimers) clearTimeout(timer);
        this.pendingTimers.clear();
        if (error) reject(error);
        else resolve();
      });
    });
  }

  get baseUrl() {
    const address = this.server.address();
    return `http://${address.address}:${address.port}`;
  }

  _faultLog(entry) {
    const record = { ts: Date.now(), ...entry };
    if (this.logPath) appendFileSync(this.logPath, `${JSON.stringify(record)}\n`);
    return record;
  }

  enqueue(type, count, extra = {}) {
    for (let i = 0; i < count; i += 1) {
      const withDefaults = type === "timeout"
        ? { delayMs: this.timeoutDelayMs, ...extra }
        : extra;
      this.queue.push({ type, ...withDefaults });
    }
    this._faultLog({ event: "enqueue", type, count });
  }

  recover() {
    const cleared = this.queue.splice(0);
    this._faultLog({ event: "recover", cleared: cleared.length });
    return cleared.length;
  }

  status() {
    return { queue: [...this.queue], counters: { ...this.counters } };
  }

  async _route(req, res) {
    const url = new URL(req.url, "http://localhost");
    try {
      if (req.method === "GET" && url.pathname === "/manifest") {
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify(this.manifest));
        return;
      }
      if (req.method === "GET" && url.pathname.startsWith("/segments/")) {
        await this._serveSegment(url, res);
        return;
      }
      if (req.method === "POST" && url.pathname === "/faults/recover") {
        const cleared = this.recover();
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ ok: true, cleared }));
        return;
      }
      if (req.method === "POST" && url.pathname.startsWith("/faults/")) {
        await this._applyFault(url, req, res);
        return;
      }
      if (req.method === "GET" && url.pathname === "/faults/status") {
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify(this.status()));
        return;
      }
      res.writeHead(404).end("not found");
    } catch (error) {
      res.writeHead(500, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: error.message }));
    }
  }

  async _applyFault(url, req, res) {
    const body = await readJson(req);
    const count = Math.max(1, Number(body.count) || 1);
    const type = url.pathname.split("/").pop();
    if (!["timeout", "corrupt", "reset"].includes(type)) {
      res.writeHead(400).end(JSON.stringify({ error: "unknown fault" }));
      return;
    }
    const extra = type === "timeout"
      ? { delayMs: Number(body.delayMs) || this.timeoutDelayMs }
      : {};
    this.enqueue(type, count, extra);
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ ok: true, type, queued: count }));
  }

  async _serveSegment(url, res) {
    const seq = Number(url.pathname.split("/").pop());
    const bitrate = Number(url.searchParams.get("bitrate")) || 0;
    const fault = this.queue.shift() || null;

    if (fault) {
      this.counters[fault.type] += 1;
      this._faultLog({ event: "inject", type: fault.type, seq, bitrate });
      if (fault.type === "reset") {
        res.socket.destroy();
        return;
      }
      if (fault.type === "timeout") {
        // Respond only after delayMs. A client whose request timeout is
        // shorter than this delay aborts first and reports a segment timeout.
        const timer = setTimeout(() => {
          this.pendingTimers.delete(timer);
          if (!res.writableEnded) {
            res.writeHead(200, { "content-type": "application/octet-stream" });
            res.end(this._buildSegment(seq, bitrate, false));
          }
        }, fault.delayMs);
        this.pendingTimers.add(timer);
        res.on("close", () => {
          clearTimeout(timer);
          this.pendingTimers.delete(timer);
        });
        return;
      }
      if (fault.type === "corrupt") {
        const broken = this._buildSegment(seq, bitrate, true);
        res.writeHead(200, { "content-type": "application/octet-stream" });
        res.end(broken);
        return;
      }
    }

    this.counters.served += 1;
    res.writeHead(200, { "content-type": "application/octet-stream" });
    res.end(this._buildSegment(seq, bitrate, false));
  }

  _buildSegment(seq, bitrate, corrupt) {
    const { segmentDuration } = this.manifest;
    const start = seq * segmentDuration;
    const frames = Array.from({ length: 4 }, (_, i) => ({
      pts: start + (i * segmentDuration) / 4,
      key: i === 0,
    }));
    const buffer = encodeSegment(
      { seq, start, duration: segmentDuration, bitrate },
      { frames },
    );
    if (!corrupt) return buffer;
    // Flip payload bytes; header CRC then mismatches and decoding fails.
    buffer[HEADER_SIZE + 2] ^= 0xff;
    buffer[HEADER_SIZE + 3] ^= 0x0f;
    return buffer;
  }
}
