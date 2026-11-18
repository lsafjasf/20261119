import test from "node:test";
import assert from "node:assert/strict";
import { VideoFrameOwnershipTracker } from "../src/tracker.js";
import { SimulatedVideoFrame } from "../src/simulated-frame.js";

test("records create, clone, worker transfer, and close with stable frame IDs", () => {
  const tracker = new VideoFrameOwnershipTracker({ scenarioId: "unit", idPrefix: "VF-U" });
  const source = new SimulatedVideoFrame({ label: "source" });
  const sourceId = tracker.create(source, { stage: "create", owner: "decoder" });
  const clone = source.clone();
  const cloneId = tracker.clone(source, clone, { stage: "clone", owner: "decoder" });

  tracker.transferOut(source, { from: "decoder", to: "renderer", stage: "post" });
  tracker.acceptTransfer(sourceId, source, { stage: "accept" });
  tracker.close(clone, { stage: "close-clone" });
  tracker.close(source, { stage: "close-source" });
  tracker.markStage("final");

  const scenario = tracker.exportScenario({ title: "unit" });
  assert.deepEqual(
    scenario.frames.map((frame) => frame.id),
    [sourceId, cloneId],
  );
  assert.deepEqual(
    scenario.events.map((event) => event.type),
    [
      "create",
      "clone",
      "transfer-out",
      "transfer-in",
      "close",
      "close",
      "stage",
    ],
  );
  assert.equal(scenario.frames[1].parentId, sourceId);
  assert.equal(scenario.cleanup.clean, true);
  assert.equal(scenario.cleanup.closeEventCount, 2);
  assert.equal(scenario.liveness.strongFrameReferencesInTracker, 0);
});

test("stage snapshots distinguish live and in-transit ownership", () => {
  const tracker = new VideoFrameOwnershipTracker({ scenarioId: "snapshot", idPrefix: "VF-S" });
  const frame = new SimulatedVideoFrame();
  tracker.create(frame, { stage: "create", owner: "decoder" });
  tracker.markStage("decoded");
  tracker.transferOut(frame, { from: "decoder", to: "renderer", stage: "posted" });
  const inFlight = tracker.markStage("in-flight");
  tracker.acceptTransfer("VF-S-001", frame, { stage: "accepted" });
  tracker.close(frame, { stage: "closed" });
  const final = tracker.markStage("final");

  assert.equal(inFlight.liveCount, 0);
  assert.equal(inFlight.inTransitCount, 1);
  assert.deepEqual(inFlight.byOwner, { renderer: 1 });
  assert.equal(final.openCount, 0);
});

test("diagnostics point to exact stages and event indexes", () => {
  const tracker = new VideoFrameOwnershipTracker({ scenarioId: "fault", idPrefix: "VF-F" });
  const frame = new SimulatedVideoFrame();
  tracker.create(frame, { stage: "create" });
  tracker.close(frame, { stage: "close" });
  tracker.close(frame, { stage: "close-again" });
  tracker.use(frame, { stage: "use-again", operation: "render" });
  const leaked = new SimulatedVideoFrame();
  tracker.create(leaked, { stage: "create-leak" });
  tracker.inspectUnreleased({ stage: "leak-check" });

  const scenario = tracker.exportScenario({ title: "fault" });
  const diagnostics = scenario.diagnostics.map(({ code, frameId, stage, eventIndex }) => ({
    code,
    frameId,
    stage,
    eventIndex,
  }));
  assert.deepEqual(diagnostics, [
    { code: "double-close", frameId: "VF-F-001", stage: "close-again", eventIndex: 2 },
    { code: "use-after-close", frameId: "VF-F-001", stage: "use-again", eventIndex: 3 },
    { code: "unreleased-frame", frameId: "VF-F-002", stage: "leak-check", eventIndex: 5 },
  ]);
  for (const diagnostic of scenario.diagnostics) {
    const event = scenario.events[diagnostic.eventIndex];
    assert.equal(event.type, "warning");
    assert.equal(event.code, diagnostic.code);
    assert.equal(event.stage, diagnostic.stage);
    assert.equal(event.frameId, diagnostic.frameId);
  }
});

test("exported events never include frame objects", () => {
  const tracker = new VideoFrameOwnershipTracker({ scenarioId: "shape", idPrefix: "VF-P" });
  const frame = new SimulatedVideoFrame({ label: "secret-object" });
  tracker.create(frame, { stage: "create", owner: "decoder" });
  const serialized = JSON.stringify(tracker.exportScenario({ title: "shape" }));
  assert.equal(serialized.includes(frame.objectMarker), false);
  assert.equal(serialized.includes('"close":'), false);
});
