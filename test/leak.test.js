import test from "node:test";
import assert from "node:assert/strict";
import { VideoFrameOwnershipTracker } from "../src/tracker.js";
import { SimulatedVideoFrame } from "../src/simulated-frame.js";

function trackedFrameWithWeakReference() {
  const tracker = new VideoFrameOwnershipTracker({ scenarioId: "gc", idPrefix: "VF-G" });
  let frame = new SimulatedVideoFrame({ label: "garbage-collection-probe" });
  tracker.create(frame, { stage: "create", owner: "decoder-worker" });
  const weakFrame = new WeakRef(frame);
  frame = null;
  return { tracker, weakFrame };
}

async function forceGarbageCollection() {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    global.gc();
    await new Promise((resolve) => setImmediate(resolve));
  }
}

test(
  "tracker weak references do not keep a closed-or-open frame alive",
  { skip: typeof global.gc !== "function" },
  async () => {
    const { tracker, weakFrame } = trackedFrameWithWeakReference();
    await forceGarbageCollection();
    assert.equal(weakFrame.deref(), undefined);
    assert.equal(tracker.liveness().reachableWeakTargets, 0);
    assert.equal(tracker.liveness().strongFrameReferencesInTracker, 0);
  },
);
