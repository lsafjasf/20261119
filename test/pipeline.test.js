import test from "node:test";
import assert from "node:assert/strict";
import { runDemo } from "../src/pipeline.js";
import { renderReport } from "../src/report.js";

const data = runDemo();
const byId = new Map(data.scenarios.map((scenario) => [scenario.id, scenario]));
const stage = (scenario, label) => scenario.stages.find((item) => item.label === label);

test("normal pipeline releases every frame and reports zero residual", () => {
  const scenario = byId.get("normal");
  assert.equal(stage(scenario, "decode:create").liveCount, 1);
  assert.equal(stage(scenario, "fanout:clone").liveCount, 2);
  assert.equal(stage(scenario, "transfer:posted").inTransitCount, 1);
  assert.equal(stage(scenario, "render:source-close").openCount, 0);
  assert.equal(scenario.cleanup.clean, true);
  assert.equal(scenario.cleanup.closeEventCount, 2);
  assert.equal(scenario.diagnostics.length, 0);
});

test("cancellation snapshot keeps two in-flight frames before draining", () => {
  const scenario = byId.get("cancelled");
  const cancelled = stage(scenario, "cancel:in-flight");
  assert.equal(cancelled.openCount, 2);
  assert.equal(cancelled.inTransitCount, 2);
  assert.equal(cancelled.liveCount, 0);
  assert.equal(stage(scenario, "cancel:drain").openCount, 0);
  assert.equal(scenario.cleanup.clean, true);
  assert.deepEqual(
    scenario.diagnostics.map(({ code, stage: diagnosticStage }) => [code, diagnosticStage]),
    [
      ["unreleased-frame", "cancel:in-flight"],
      ["unreleased-frame", "cancel:in-flight"],
    ],
  );
});

test("abnormal exit leaves one orphan frame, then recovery closes it", () => {
  const scenario = byId.get("abnormal");
  assert.equal(stage(scenario, "worker:crash").openCount, 1);
  assert.deepEqual(stage(scenario, "worker:crash").openFrameIds, ["VF-A-001"]);
  assert.equal(stage(scenario, "recovery:close-orphan").openCount, 0);
  assert.equal(scenario.cleanup.clean, true);
  assert.equal(scenario.cleanup.residualCount, 0);
  assert.deepEqual(
    scenario.diagnostics.map(({ code, frameId, stage: diagnosticStage }) => ({
      code,
      frameId,
      stage: diagnosticStage,
    })),
    [{ code: "unreleased-frame", frameId: "VF-A-001", stage: "worker:crash" }],
  );
});

test("fault injection verifies all required warnings at expected locations", () => {
  const scenario = byId.get("fault-injection");
  assert.deepEqual(
    scenario.diagnostics.map(({ code, stage: diagnosticStage }) => [code, diagnosticStage]),
    [
      ["double-close", "fault:double-close"],
      ["use-after-close", "fault:use-after-close"],
      ["unreleased-frame", "fault:unreleased"],
    ],
  );
  for (const diagnostic of scenario.diagnostics) {
    const event = scenario.events[diagnostic.eventIndex];
    assert.equal(event.code, diagnostic.code);
    assert.equal(event.stage, diagnostic.stage);
  }
  assert.equal(scenario.cleanup.clean, true);
});

test("demo summary is fully cleaned and report is expandable", () => {
  assert.equal(data.summary.pipelinePaths, 3);
  assert.equal(data.summary.framesCreated, 9);
  assert.equal(data.summary.framesClosed, 9);
  assert.equal(data.summary.residualFrames, 0);
  assert.equal(data.summary.pipelineResidualFrames, 0);
  assert.equal(data.summary.allClean, true);

  const html = renderReport(data);
  assert.match(html, /<details class="scenario" open>/);
  assert.match(html, /<details class="frame">/);
  assert.match(html, /VF-N-001/);
  assert.match(html, /fault:double-close/);
  assert.match(html, /弱引用记录/);
});
