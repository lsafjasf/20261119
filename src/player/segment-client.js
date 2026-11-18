import {
  DecodeError,
  NetworkInterruptionError,
  SegmentTimeoutError,
} from "../recovery/errors.js";
import { decodeSegment } from "../recovery/protocol.js";

async function fetchBuffer(url, token) {
  const isTimedOut = () =>
    Boolean(token && (token.signal.aborted || (token.deadline && Date.now() >= token.deadline)));
  let response;
  try {
    response = await fetch(url, token ? { signal: token.signal } : undefined);
  } catch (error) {
    if (isTimedOut()) throw error;
    // ECONNREFUSED / ECONNRESET / DNS failure all surface here.
    throw new NetworkInterruptionError(error.message || String(error));
  }
  if (!response.ok) {
    throw new NetworkInterruptionError(`HTTP ${response.status} ${response.statusText}`);
  }
  try {
    return Buffer.from(await response.arrayBuffer());
  } catch (error) {
    if (isTimedOut()) throw error;
    throw new NetworkInterruptionError(error.message || String(error));
  }
}

// Fetches and decodes one media segment. Timeouts and socket failures are
// converted into the typed errors understood by the recovery engine.
export class SegmentClient {
  constructor({ baseUrl, requestTimeoutMs = 800, fetchImpl = fetchBuffer }) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.requestTimeoutMs = requestTimeoutMs;
    this.fetchImpl = fetchImpl;
  }

  async getManifest() {
    const raw = await this.fetchImpl(`${this.baseUrl}/manifest`, null);
    return JSON.parse(raw.toString("utf8"));
  }

  async getSegment(seq, bitrate) {
    const controller = new AbortController();
    const deadline = Date.now() + this.requestTimeoutMs;
    const token = { signal: controller.signal, deadline };
    const timer = setTimeout(() => controller.abort(), this.requestTimeoutMs);
    const url = `${this.baseUrl}/segments/${seq}?bitrate=${bitrate}`;
    try {
      const buffer = await this.fetchImpl(url, token);
      try {
        return decodeSegment(buffer);
      } catch (error) {
        throw new DecodeError(seq, error.message);
      }
    } catch (error) {
      if (controller.signal.aborted || Date.now() >= deadline) {
        throw new SegmentTimeoutError(seq, this.requestTimeoutMs);
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }
}
