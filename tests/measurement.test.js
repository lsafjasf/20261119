import assert from "node:assert/strict";
import test from "node:test";
import { runMeasurementSuite } from "../src/measurement.js";

test("measurement suite covers delay accuracy, response and bounded jumps", async () => {
  const results = await runMeasurementSuite();

  for (const mode of ["linear", "cubic"]) {
    assert.ok(
      Math.abs(results.impulse[mode].metrics.errorSamples) < 1e-6,
      `${mode} impulse delay error is too large`,
    );
    assert.ok(Number.isFinite(results.sweep[mode].metrics.db10k));
    assert.ok(Number.isFinite(results.sweep[mode].metrics.db18k));
    assert.equal(results.jump[mode].metrics.stable, true);
    assert.equal(results.jump[mode].metrics.nonFinite, 0);
    assert.ok(results.jump[mode].metrics.maxAbs <= 1.2000001);
  }

  assert.ok(
    results.sweep.cubic.metrics.db18k > results.sweep.linear.metrics.db18k,
    "cubic interpolation should retain more energy at 18 kHz",
  );
});
