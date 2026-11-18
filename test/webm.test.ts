import { describe, expect, it } from "vitest";
import { inspectWebM, measuredDurationMs } from "../src/webm";

function id(hex: string): number[] {
  return [...hex.matchAll(/../g)].map((part) => Number.parseInt(part[0], 16));
}

function size(value: number): number[] {
  if (value < 0x7f) return [0x80 | value];
  if (value < 0x3fff) return [0x40 | (value >> 8), value & 0xff];
  throw new Error("Fixture size is too large");
}

function element(elementId: string, payload: number[]): number[] {
  return [...id(elementId), ...size(payload.length), ...payload];
}

function uintBytes(value: number, length = 1): number[] {
  const bytes = new Array<number>(length).fill(0);
  let remaining = value;
  for (let index = length - 1; index >= 0; index -= 1) {
    bytes[index] = remaining & 0xff;
    remaining >>= 8;
  }
  return bytes;
}

function float64(value: number): number[] {
  const bytes = new Uint8Array(8);
  new DataView(bytes.buffer).setFloat64(0, value);
  return [...bytes];
}

function trackEntry(number: number, type: number, codec: string, extra: number[]): number[] {
  return element("AE", [
    ...element("D7", uintBytes(number)),
    ...element("83", uintBytes(type)),
    ...element("86", [...new TextEncoder().encode(codec)]),
    ...extra,
  ]);
}

function simpleBlock(trackNumber: number, relativeTimecode: number): number[] {
  return element("A3", [
    0x80 | trackNumber,
    (relativeTimecode >> 8) & 0xff,
    relativeTimecode & 0xff,
    0x80,
    1,
    2,
    3,
  ]);
}

function fixture(withDuration = true): ArrayBuffer {
  const info = element("1549A966", [
    ...element("2AD7B1", uintBytes(1_000_000, 3)),
    ...(withDuration ? element("4489", float64(2_000)) : []),
  ]);
  const tracks = element("1654AE6B", [
    ...trackEntry(
      1,
      1,
      "V_VP8",
      element("E0", [
        ...element("B0", uintBytes(320, 2)),
        ...element("BA", uintBytes(180, 2)),
      ]),
    ),
    ...trackEntry(
      2,
      2,
      "A_OPUS",
      element("E1", [
        ...element("9F", uintBytes(2)),
        ...element("B5", float64(48_000)),
      ]),
    ),
  ]);
  const cluster = element("1F43B675", [
    ...element("E7", uintBytes(0)),
    ...simpleBlock(1, 0),
    ...simpleBlock(1, 1_000),
    ...simpleBlock(2, 1_000),
  ]);
  const segment = [...id("18538067"), 0x01, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff];
  return new Uint8Array([...segment, ...info, ...tracks, ...cluster]).buffer;
}

describe("inspectWebM", () => {
  it("reads duration, video dimensions and audio channels", () => {
    const result = inspectWebM(fixture());
    expect(result.durationMs).toBe(2_000);
    expect(result.video?.codecId).toBe("V_VP8");
    expect(result.video?.width).toBe(320);
    expect(result.video?.height).toBe(180);
    expect(result.audio?.codecId).toBe("A_OPUS");
    expect(result.audio?.channels).toBe(2);
    expect(result.audio?.sampleRate).toBe(48_000);
    expect(result.videoFrames).toBe(2);
    expect(result.audioBlocks).toBe(1);
    expect(measuredDurationMs(result)).toBe(2_000);
  });

  it("falls back to cluster timestamps when Duration is absent", () => {
    const result = inspectWebM(fixture(false));
    expect(result.durationMs).toBeNull();
    expect(result.maxBlockTimestampMs).toBe(1_000);
    expect(measuredDurationMs(result)).toBe(1_000);
    expect(result.warnings.join(" ")).toContain("Duration");
  });

  it("rejects data without a Segment", () => {
    const headerOnly = new Uint8Array(element("1A45DFA3", [])).buffer;
    expect(() => inspectWebM(headerOnly)).toThrow(/Segment/);
  });
});
