import test from "node:test";
import assert from "node:assert/strict";
import { parseRawArray } from "../src/raw-input.js";

test("parses a flat declared RAW array and normalizes 8-bit values", () => {
  const result = parseRawArray(JSON.stringify({
    pattern: "BGGR",
    width: 2,
    height: 2,
    data: [0, 64, 128, 255],
  }));
  assert.equal(result.pattern, "BGGR");
  assert.equal(result.width, 2);
  assert.equal(result.height, 2);
  assert.equal(result.scale, 255);
  assert.deepEqual([...result.raw].map((value) => Number(value.toFixed(4))), [0, 0.251, 0.502, 1]);
});

test("derives dimensions from a two-dimensional data array", () => {
  const result = parseRawArray(JSON.stringify({
    pattern: "RGGB",
    data: [[0.1, 0.2], [0.3, 0.4]],
  }));
  assert.equal(result.width, 2);
  assert.equal(result.height, 2);
  assert.equal(result.scale, 1);
  assert.ok(Math.abs(result.raw[3] - 0.4) < 1e-6);
});

test("supports 12-bit data through bitDepth", () => {
  const result = parseRawArray(JSON.stringify({
    pattern: "RGGB",
    width: 2,
    height: 1,
    bitDepth: 12,
    data: [0, 4095],
  }));
  assert.equal(result.scale, 4095);
  assert.equal(result.raw[1], 1);
});

test("rejects arrays without an explicit pattern declaration", () => {
  assert.throws(
    () => parseRawArray(JSON.stringify([[0.1, 0.2], [0.3, 0.4]])),
    /缺少排列声明/,
  );
});

test("rejects invalid lengths and values", () => {
  assert.throws(
    () => parseRawArray(JSON.stringify({ pattern: "RGGB", width: 3, height: 3, data: [0, 1] })),
    /长度应为 9/,
  );
  assert.throws(
    () => parseRawArray(JSON.stringify({ pattern: "RGGB", width: 1, height: 1, data: [-1] })),
    /不能为负数/,
  );
});
