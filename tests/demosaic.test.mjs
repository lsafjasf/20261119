import test from "node:test";
import assert from "node:assert/strict";
import {
  cfaAt,
  demosaicBilinear,
  demosaicEdgeDirected,
  explainPixel,
  interpolationWeights,
  mosaicFromRgb,
} from "../src/demosaic.js";
import { computeMetrics } from "../src/metrics.js";
import { createTestScene } from "../src/scene.js";

function guardedRaw(raw, width, height) {
  return new Proxy(raw, {
    get(target, property, receiver) {
      if (typeof property === "string" && /^\d+$/.test(property)) {
        const index = Number(property);
        if (index < 0 || index >= width * height) {
          throw new RangeError(`Out-of-bounds read at ${index}`);
        }
      }
      return Reflect.get(target, property, receiver);
    },
  });
}

function weightedValue(raw, width, entries) {
  return entries.reduce((sum, term) => sum + raw[term.y * width + term.x] * term.weight, 0);
}

function clamp01(value) {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

test("CFA mapping follows RGGB and BGGR declarations", () => {
  assert.deepEqual(
    [cfaAt("RGGB", 0, 0), cfaAt("RGGB", 1, 0), cfaAt("RGGB", 0, 1), cfaAt("RGGB", 1, 1)],
    ["R", "G", "G", "B"],
  );
  assert.deepEqual(
    [cfaAt("BGGR", 0, 0), cfaAt("BGGR", 1, 0), cfaAt("BGGR", 0, 1), cfaAt("BGGR", 1, 1)],
    ["B", "G", "G", "R"],
  );
});

test("mosaic uses the sampled channel at every site", () => {
  const width = 4;
  const height = 4;
  const truth = {
    r: Float32Array.from({ length: 16 }, (_, i) => i / 20),
    g: Float32Array.from({ length: 16 }, (_, i) => i / 24),
    b: Float32Array.from({ length: 16 }, (_, i) => i / 28),
  };
  const raw = mosaicFromRgb(truth, width, height, "RGGB");
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const cfa = cfaAt("RGGB", x, y);
      const expected = cfa === "R" ? truth.r : cfa === "G" ? truth.g : truth.b;
      assert.equal(raw[y * width + x], expected[y * width + x]);
    }
  }
});

test("both decoders never read outside the raw array", () => {
  const width = 9;
  const height = 7;
  const pattern = "BGGR";
  const truth = createTestScene(width, height);
  const raw = guardedRaw(mosaicFromRgb(truth, width, height, pattern), width, height);
  const bilinear = demosaicBilinear(raw, width, height, pattern);
  const edge = demosaicEdgeDirected(raw, width, height, pattern);
  assert.equal(bilinear.r.length, width * height);
  assert.equal(edge.b.length, width * height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      explainPixel(raw, width, height, pattern, x, y);
      for (const channel of ["R", "G", "B"]) {
        interpolationWeights(raw, width, height, pattern, x, y, "bilinear", channel);
        interpolationWeights(raw, width, height, pattern, x, y, "edge", channel);
      }
    }
  }
});

test("explanation values and weights reproduce decoder output", () => {
  const width = 13;
  const height = 11;
  const pattern = "RGGB";
  const truth = createTestScene(width, height);
  const raw = mosaicFromRgb(truth, width, height, pattern);
  const bilinear = demosaicBilinear(raw, width, height, pattern);
  const edge = demosaicEdgeDirected(raw, width, height, pattern);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const index = y * width + x;
      const explanation = explainPixel(raw, width, height, pattern, x, y);
      assert.ok(Math.abs(explanation.bilinear.R - bilinear.r[index]) < 1e-6);
      assert.ok(Math.abs(explanation.edge.B - edge.b[index]) < 1e-6);
      for (const [method, out] of [["bilinear", bilinear], ["edge", edge]]) {
        for (const [channel, plane] of [["R", out.r], ["G", out.g], ["B", out.b]]) {
          const weights = interpolationWeights(raw, width, height, pattern, x, y, method, channel);
          assert.ok(Math.abs(weights.sum - 1) < 1e-6, `${method} ${channel} weights should sum to 1`);
          assert.ok(
            Math.abs(clamp01(weightedValue(raw, width, weights.entries)) - plane[index]) < 1e-5,
          );
        }
      }
    }
  }
});

test("edge-directed reconstruction improves a full test-scene decode", () => {
  const width = 96;
  const height = 72;
  const truth = createTestScene(width, height);
  const raw = mosaicFromRgb(truth, width, height, "RGGB");
  const bilinear = demosaicBilinear(raw, width, height, "RGGB");
  const edge = demosaicEdgeDirected(raw, width, height, "RGGB");
  const baseline = computeMetrics(truth, bilinear, width, height);
  const improved = computeMetrics(truth, edge, width, height);
  assert.ok(improved.psnr > baseline.psnr, "edge decoder should improve PSNR");
  assert.ok(improved.rgbMae < baseline.rgbMae, "edge decoder should reduce RGB MAE");
  assert.ok(improved.fringeShare < baseline.fringeShare, "edge decoder should reduce fringe pixels");
  assert.ok(improved.zipperScore < baseline.zipperScore, "edge decoder should reduce zippering");
});

test("a declaration mismatch produces a visibly worse result", () => {
  const width = 96;
  const height = 72;
  const truth = createTestScene(width, height);
  const raw = mosaicFromRgb(truth, width, height, "RGGB");
  const matched = demosaicEdgeDirected(raw, width, height, "RGGB");
  const mismatched = demosaicEdgeDirected(raw, width, height, "BGGR");
  const matchedMetrics = computeMetrics(truth, matched, width, height);
  const mismatchMetrics = computeMetrics(truth, mismatched, width, height);
  assert.ok(mismatchMetrics.rgbMae > matchedMetrics.rgbMae * 1.8);
});
