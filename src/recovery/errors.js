// Failure taxonomy for playback auto-recovery.

export const FailureReason = Object.freeze({
  SEGMENT_TIMEOUT: "segment_timeout",
  DECODE_ERROR: "decode_error",
  NETWORK_INTERRUPTION: "network_interruption",
  BUFFER_STARVATION: "buffer_starvation",
});

export class SegmentTimeoutError extends Error {
  constructor(seq, timeoutMs) {
    super(`segment ${seq} timed out after ${timeoutMs}ms`);
    this.name = "SegmentTimeoutError";
    this.reason = FailureReason.SEGMENT_TIMEOUT;
    this.seq = seq;
    this.timeoutMs = timeoutMs;
  }
}

export class DecodeError extends Error {
  constructor(seq, detail) {
    super(`decode failure on segment ${seq}: ${detail}`);
    this.name = "DecodeError";
    this.reason = FailureReason.DECODE_ERROR;
    this.seq = seq;
  }
}

export class NetworkInterruptionError extends Error {
  constructor(detail) {
    super(`network interruption: ${detail}`);
    this.name = "NetworkInterruptionError";
    this.reason = FailureReason.NETWORK_INTERRUPTION;
  }
}

export function classifyError(error) {
  if (error && typeof error.reason === "string") {
    return error.reason;
  }
  // Fetch-level failures (ECONNREFUSED, socket reset, abort) map to a
  // network interruption unless a more specific type was thrown.
  return FailureReason.NETWORK_INTERRUPTION;
}
