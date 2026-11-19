"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const M = require("../js/dct.js");
const P = require("../js/patterns.js");

function blockFromPlane(sample, plane, blockX, blockY) {
  const values = [];
  for (let y = 0; y < 8; y += 1) {
    for (let x = 0; x < 8; x += 1) {
      values.push(sample.planes[plane][(blockY * 8 + y) * 128 + blockX * 8 + x]);
    }
  }
  return values;
}

test("forward and inverse DCT round-trip floating point blocks", () => {
  const source = Float64Array.from({ length: 64 }, (_, index) => {
    const x = index % 8;
    const y = Math.floor(index / 8);
    return 32 + x * 11 + y * 7 + ((x + y) % 3) * 4;
  });
  const coeffs = M.fdctBlock(source, 0);
  const reconstructed = M.idctBlock(coeffs, 0);
  let maxError = 0;
  for (let i = 0; i < source.length; i += 1) {
    maxError = Math.max(maxError, Math.abs(source[i] - reconstructed[i]));
  }
  assert.ok(maxError < 1e-9, `round-trip error was ${maxError}`);
});

test("DC coefficient uses the orthonormal JPEG scaling", () => {
  const block = new Array(64).fill(128);
  const coeffs = M.fdctBlock(block, 128);
  assert.equal(coeffs[0], 0);

  block.fill(138);
  const shifted = M.fdctBlock(block, 128);
  assert.ok(Math.abs(shifted[0] - 80) < 1e-9);
});

test("high-frequency checkerboard places almost all energy above the mid band", () => {
  const checker = Array.from({ length: 64 }, (_, index) => {
    const x = index % 8;
    const y = Math.floor(index / 8);
    return (x + y) % 2 ? 230 : 28;
  });
  const coeffs = M.fdctBlock(checker, 128);
  const energy = M.bandEnergy(coeffs, coeffs);
  assert.ok(energy.highRawShare > 0.95);
  assert.ok(energy.bands[0].rawShare < 0.01);
});

test("flat blocks concentrate DCT energy in DC", () => {
  const flat = new Array(64).fill(143);
  const coeffs = M.fdctBlock(flat, 128);
  const energy = M.bandEnergy(coeffs, coeffs);
  assert.ok(energy.bands[0].rawShare > 0.999999);
});

test("coarse quantization reduces nonzero coefficient count", () => {
  const sample = P.createSample("diagonal");
  const block = blockFromPlane(sample, "Y", 6, 6);
  const fine = M.reconstructBlock(block, M.makeUnitTable());
  const coarse = M.reconstructBlock(block, M.makeQuantTable("luma", 20));
  const fineCount = Array.from(fine.quantized).filter(Boolean).length;
  const coarseCount = Array.from(coarse.quantized).filter(Boolean).length;
  assert.ok(coarseCount < fineCount);
});

test("Q=1 still exposes an 8-bit round-trip difference", () => {
  const sample = P.createSample("diagonal");
  const block = blockFromPlane(sample, "Y", 6, 6);
  const result = M.reconstructBlock(block, M.makeUnitTable());
  assert.ok(result.error.mae > 0);
  assert.ok(result.error.max >= 1);
  assert.ok(result.error.changed > 0);
});

test("boundary error compares boundary gradients rather than raw pixels", () => {
  const original = new Uint8ClampedArray(16 * 8);
  const reconstructed = new Uint8ClampedArray(16 * 8);
  for (let y = 0; y < 8; y += 1) {
    for (let x = 0; x < 16; x += 1) {
      original[y * 16 + x] = x < 8 ? 20 : 170;
      reconstructed[y * 16 + x] = 20 + x * 4;
    }
  }
  const error = M.boundaryError(original, reconstructed, 16, 8, 8);
  assert.ok(error.mae > 0);
  assert.equal(error.count, 8);
});

test("selected-block boundary error only samples the four neighboring seams", () => {
  const original = new Uint8ClampedArray(24 * 24);
  const reconstructed = new Uint8ClampedArray(24 * 24);
  for (let i = 0; i < original.length; i += 1) {
    original[i] = i % 24 < 8 ? 30 : 180;
    reconstructed[i] = original[i] + (i % 2 === 0 ? 1 : 0);
  }
  const error = M.blockBoundaryError(original, reconstructed, 24, 24, 1, 1, 8);
  assert.equal(error.count, 32);
  assert.ok(error.mae > 0);
});

test("standard tables produce step 1 at JPEG quality 100", () => {
  const luma = M.makeQuantTable("luma", 100);
  const chroma = M.makeQuantTable("chroma", 100);
  assert.ok(luma.every((value) => value === 1));
  assert.ok(chroma.every((value) => value === 1));
});
