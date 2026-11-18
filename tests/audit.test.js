const test = require("node:test");
const assert = require("node:assert/strict");
const data = require("../src/data.js");
const Audit = require("../src/audit.js");

function auditData(source = data) {
  return Audit.buildAudit(JSON.parse(JSON.stringify(source)));
}

function findClip(audit, clipId) {
  return audit.clips.find((entry) => entry.clipId === clipId);
}

test("summary includes every requested state", () => {
  const audit = auditData();
  assert.deepEqual(audit.summary, {
    total: 6,
    available: 2,
    conflict: 2,
    missing: 1,
    ambiguous: 1,
    blocked: 2,
    differences: 6,
  });
});

test("exact metadata and fingerprint is available", () => {
  const entry = findClip(auditData(), "CLIP-01");
  assert.equal(entry.status, "available");
  assert.equal(entry.traceable, true);
  assert.equal(entry.diffCount, 0);
  assert.equal(entry.fields.length, 5);
});

test("metadata drift is reported field by field", () => {
  const entry = findClip(auditData(), "CLIP-02");
  assert.equal(entry.status, "conflict");
  assert.deepEqual(
    entry.differences.map((difference) => difference.key),
    ["startFrame", "durationFrames"]
  );
  assert.equal(entry.traceable, true);
});

test("frame-rate and duration conflicts are both visible", () => {
  const entry = findClip(auditData(), "CLIP-04");
  assert.equal(entry.status, "conflict");
  assert.deepEqual(
    entry.differences.map((difference) => difference.key),
    ["frameRate", "durationFrames"]
  );
});

test("missing proxy blocks playback instead of creating empty media", () => {
  const entry = findClip(auditData(), "CLIP-06");
  assert.equal(entry.status, "missing");
  assert.equal(entry.proxy, null);
  assert.equal(entry.traceable, false);
  assert.equal(entry.playbackBlocked, true);
  assert.equal(entry.differences[0].type, "missing");
  assert.match(entry.differences[0].message, /silence|empty frame/);
});

test("same-name different-content files stay ambiguous", () => {
  const entry = findClip(auditData(), "CLIP-05");
  assert.equal(entry.status, "ambiguous");
  assert.equal(entry.proxy, null);
  assert.equal(entry.traceable, false);
  assert.equal(entry.playbackBlocked, true);
  assert.equal(entry.candidates.length, 2);
  assert.equal(entry.candidates[0].filename, entry.candidates[1].filename);
  assert.notEqual(entry.candidates[0].signature, entry.candidates[1].signature);
  assert.equal(entry.mapping.method, "Withheld · filename collision");
});

test("a lone filename candidate still waits for confirmation", () => {
  const singleCandidate = JSON.parse(JSON.stringify(data));
  singleCandidate.clips.find((clip) => clip.id === "CLIP-05").candidateIds = ["PRX-005A"];
  const entry = findClip(auditData(singleCandidate), "CLIP-05");
  assert.equal(entry.status, "ambiguous");
  assert.equal(entry.proxy, null);
  assert.equal(entry.candidates.length, 1);
});

test("a user confirmation is required before an ambiguous candidate maps", () => {
  const resolved = Audit.resolveClipCandidate(data, "CLIP-05", "PRX-005A");
  const entry = findClip(auditData(resolved), "CLIP-05");
  assert.equal(entry.status, "available");
  assert.equal(entry.proxy.id, "PRX-005A");
  assert.equal(entry.resolvedByUser, true);
  assert.equal(entry.traceable, true);
});

test("a user-confirmed wrong candidate remains a conflict", () => {
  const resolved = Audit.resolveClipCandidate(data, "CLIP-05", "PRX-005B");
  const entry = findClip(auditData(resolved), "CLIP-05");
  assert.equal(entry.status, "conflict");
  assert.equal(entry.proxy.id, "PRX-005B");
  assert.equal(entry.differences.length, 6);
  assert.equal(entry.traceable, false);
});

test("report exposes mapping table and findings for every state", () => {
  const report = Audit.buildReport(data, { generatedAt: "2026-11-19T00:00:00.000Z" });
  assert.equal(report.checklist.length, 6);
  assert.equal(report.mappingTable.length, 6);
  assert.deepEqual(
    new Set(report.checklist.map((item) => item.status)),
    new Set(["available", "conflict", "missing", "ambiguous"])
  );
  assert.equal(report.findings.length, 6);
  assert.equal(report.traceGate.blocked, true);
});
