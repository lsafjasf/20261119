import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  applyEdit,
  parseJpeg,
  planEdit,
  targetEntries,
  TARGET_TAGS,
} from "../src/exif.js";
import { sha256 } from "../src/sha256.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sample = (name) => new Uint8Array(fs.readFileSync(path.join(root, "samples", `${name}.jpg`)));

test("sha256 matches the standard abc vector", () => {
  assert.equal(
    sha256(new TextEncoder().encode("abc")),
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  );
});

test("parses little-endian and big-endian TIFF with multiple IFDs", () => {
  for (const [name, endian] of [["canon-le", "little"], ["nikon-be", "big"]]) {
    const parsed = parseJpeg(sample(name));
    assert.equal(parsed.valid, true);
    assert.equal(parsed.exif.byteOrder, endian);
    assert.ok(parsed.exif.ifds.length >= 3);
    const date = targetEntries(parsed, TARGET_TAGS.date)[0];
    const orientation = targetEntries(parsed, TARGET_TAGS.orientation)[0];
    assert.equal(date.storage, "offset");
    assert.ok(date.dataSize >= 20);
    assert.equal(orientation.storage, "inline");
    assert.equal(parsed.scan.hash, sha256(parsed.bytes.subarray(parsed.scan.start, parsed.scan.end)));
  }
});

test("resolves nested Exif, GPS and SubIFD pointer chains", () => {
  const parsed = parseJpeg(sample("multi-ifd"));
  const paths = parsed.exif.ifds.map((ifd) => ifd.path);
  assert.ok(paths.includes("IFD0"));
  assert.ok(paths.includes("IFD0.Exif"));
  assert.ok(paths.includes("IFD0.GPS"));
  assert.ok(paths.includes("IFD0.SubIFD"));
  assert.ok(paths.includes("IFD1"));
  const pointerLinks = parsed.exif.references.filter((reference) => reference.kind === "pointer");
  assert.equal(pointerLinks.length, 3);
  assert.ok(pointerLinks.every((reference) => reference.valid));
});

test("writes only target value slots in both byte orders and preserves scan bytes", () => {
  for (const name of ["canon-le", "nikon-be", "multi-ifd"]) {
    const input = sample(name);
    const parsed = parseJpeg(input);
    const plan = planEdit(parsed, { dateText: "2025:12:31 23:59:58", orientation: 3 });
    assert.equal(plan.ok, true, `${name}: ${plan.errors.map((error) => error.message).join("; ")}`);
    const result = applyEdit(parsed, { plan });
    assert.equal(result.ok, true);
    assert.equal(result.output.length, input.length);
    assert.equal(result.verification.scan.beforeHash, result.verification.scan.afterHash);
    assert.equal(
      sha256(result.output.subarray(parsed.scan.start, parsed.scan.end)),
      parsed.scan.hash,
    );
    assert.ok(result.changedBytes.length > 0);

    const allowed = plan.patches.map((patch) => [patch.start, patch.end]);
    for (const change of result.changedBytes) {
      assert.ok(
        allowed.some(([start, end]) => change.offset >= start && change.offset < end),
        `${name}: byte ${change.offset} escaped the target slots`,
      );
    }
    const reparsed = parseJpeg(result.output);
    assert.ok(targetEntries(reparsed, TARGET_TAGS.date).every((entry) => entry.ascii === "2025:12:31 23:59:58"));
    assert.ok(targetEntries(reparsed, TARGET_TAGS.orientation).every((entry) => entry.numbers[0] === 3));
  }
});

test("detects duplicate tags and synchronously rewrites every copy", () => {
  const parsed = parseJpeg(sample("duplicate"));
  const dates = targetEntries(parsed, TARGET_TAGS.date);
  const orientations = targetEntries(parsed, TARGET_TAGS.orientation);
  assert.equal(dates.length, 2);
  assert.equal(orientations.length, 2);
  assert.ok(dates.every((entry) => entry.duplicateInIfd));
  assert.ok(orientations.every((entry) => entry.duplicateInIfd));
  assert.ok(parsed.exif.duplicateTags.some((group) => group.tag === TARGET_TAGS.date));
  assert.ok(parsed.exif.duplicateTags.some((group) => group.tag === TARGET_TAGS.orientation));

  const plan = planEdit(parsed, { dateText: "2030:01:02 03:04:05", orientation: 8 });
  assert.equal(plan.ok, true);
  assert.equal(plan.targets.date.length, 2);
  assert.equal(plan.targets.orientation.length, 2);
  assert.ok(plan.warnings.some((warning) => warning.code === "duplicate-date"));
  const result = applyEdit(parsed, { plan });
  assert.equal(result.ok, true);
  const reparsed = parseJpeg(result.output);
  assert.ok(targetEntries(reparsed, TARGET_TAGS.date).every((entry) => entry.ascii === "2030:01:02 03:04:05"));
  assert.ok(targetEntries(reparsed, TARGET_TAGS.orientation).every((entry) => entry.numbers[0] === 8));
  assert.equal(result.verification.scan.equal, true);
});

test("rejects write-out when a referenced value field is outside TIFF bounds", () => {
  const input = sample("illegal-offset");
  const parsed = parseJpeg(input);
  assert.equal(parsed.valid, false);
  assert.ok(parsed.exif.issues.some((issue) => issue.code === "data-oob"));
  const date = targetEntries(parsed, TARGET_TAGS.date)[0];
  assert.equal(date.valid, false);
  assert.ok(date.dataFileOffset > parsed.exif.tiffEnd);

  const plan = planEdit(parsed, { dateText: "2025:01:01 00:00:00", orientation: 1 });
  assert.equal(plan.ok, false);
  assert.equal(plan.canWrite, false);
  assert.ok(plan.errors.some((error) => error.code === "data-oob"));
  const result = applyEdit(parsed, { plan });
  assert.equal(result.ok, false);
  assert.equal(result.output, null);
  assert.equal(result.changedRanges.length, 0);
});

test("rejects impossible dates and capacities before constructing a patch", () => {
  const parsed = parseJpeg(sample("canon-le"));
  const badDate = planEdit(parsed, { dateText: "2025:02:30 10:00:00", orientation: 1 });
  assert.equal(badDate.ok, false);
  assert.ok(badDate.errors.some((error) => error.code === "invalid-date"));
  const badOrientation = planEdit(parsed, { dateText: "2025:02:20 10:00:00", orientation: 9 });
  assert.equal(badOrientation.ok, false);
  assert.ok(badOrientation.errors.some((error) => error.code === "invalid-orientation"));
});

test("handles a malformed target type without constructing an unsafe patch", () => {
  const input = sample("canon-le");
  const parsed = parseJpeg(input);
  const orientation = targetEntries(parsed, TARGET_TAGS.orientation)[0];
  const typeOffset = parsed.exif.tiffStart + orientation.entryOffset + 2;
  input[typeOffset] = 0xff;
  input[typeOffset + 1] = 0xff;
  const malformed = parseJpeg(input);
  assert.equal(malformed.valid, false);
  assert.ok(malformed.exif.issues.some((issue) => issue.code === "unknown-type"));
  const plan = planEdit(malformed, { dateText: "2025:01:01 00:00:00", orientation: 1 });
  assert.equal(plan.ok, false);
  assert.ok(plan.errors.some((error) => error.code === "unknown-type"));
  assert.equal(applyEdit(malformed, { plan }).output, null);
});
