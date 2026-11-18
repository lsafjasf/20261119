#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import {
  applyEdit,
  planEdit,
  parseJpeg,
  offsetLabel,
} from "../src/exif.js";

const args = process.argv.slice(2);
if (args.includes("--help") || args.includes("-h") || args.length === 0) {
  console.log(`Usage:
  node scripts/patch.mjs <input.jpg> <output.jpg> --date "YYYY:MM:DD HH:MM:SS" --orientation 1-8 [--json] [--force]

Writes only the existing DateTimeOriginal and Orientation value slots.
Out-of-bounds metadata references are rejected and no output is created.`);
  process.exit(args.length === 0 ? 1 : 0);
}

const flags = new Set();
const options = new Map();
const positional = [];
const valueOptions = new Set(["--date", "--orientation"]);
for (let index = 0; index < args.length; index += 1) {
  const arg = args[index];
  if (valueOptions.has(arg)) {
    options.set(arg, args[index + 1] ?? null);
    index += 1;
  } else if (arg.startsWith("--")) {
    flags.add(arg);
  } else {
    positional.push(arg);
  }
}
const [inputPath, outputPath] = positional;
const dateText = options.get("--date");
const orientationText = options.get("--orientation");
const json = flags.has("--json");
const force = flags.has("--force");

function fail(code, message, report = {}) {
  if (json) {
    console.log(JSON.stringify({ ok: false, code, error: message, ...report }, null, 2));
  } else {
    console.error(`PINPOINT rejected: ${message}`);
  }
  process.exit(1);
}

if (!inputPath || !outputPath) fail("usage", "需要输入与输出 JPEG 路径");
if (!dateText || !orientationText) fail("usage", "需要 --date 与 --orientation");
if (path.resolve(inputPath) === path.resolve(outputPath)) {
  fail("same-path", "输出不能覆盖输入文件");
}
if (!force && fs.existsSync(outputPath)) {
  fail("output-exists", `输出已存在：${outputPath}（使用 --force 覆盖）`);
}

let input;
try {
  input = new Uint8Array(fs.readFileSync(inputPath));
} catch (error) {
  fail("read-failed", error.message);
}

const parsed = parseJpeg(input);
const plan = planEdit(parsed, {
  dateText,
  orientation: Number(orientationText),
});
if (!plan.ok) {
  fail("write-blocked", plan.errors.map((issue) => issue.message).join("; "), {
    plan,
    parseIssues: parsed.issues,
  });
}

const result = applyEdit(parsed, { plan });
if (!result.ok || !result.verification?.passed) {
  fail("verification-failed", result.errors?.map((issue) => issue.message).join("; ") || "写后验证失败", {
    result,
  });
}

const temporaryPath = `${outputPath}.pinpoint-${process.pid}.tmp`;
try {
  fs.writeFileSync(temporaryPath, result.output);
  fs.renameSync(temporaryPath, outputPath);
} catch (error) {
  try { fs.unlinkSync(temporaryPath); } catch { /* no-op */ }
  fail("write-failed", error.message);
}

const report = {
  ok: true,
  input: inputPath,
  output: outputPath,
  dateText: plan.dateText,
  orientation: plan.orientation,
  fileSize: result.output.length,
  scan: result.verification.scan,
  changedByteCount: result.changedBytes.length,
  changedRanges: result.changedRanges.map((range) => ({
    offset: range.start,
    offsetHex: offsetLabel(range.start),
    length: range.length,
    fields: range.labels,
    before: range.beforeHex,
    after: range.afterHex,
  })),
  checks: result.verification.checks,
};

if (json) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log(`PINPOINT patched ${inputPath} → ${outputPath}`);
  console.log(`  DateTimeOriginal  ${plan.dateText}`);
  console.log(`  Orientation       ${plan.orientation}`);
  console.log(`  Scan SHA-256      ${report.scan.beforeHash}`);
  console.log(`  Scan unchanged    ${report.scan.equal ? "YES" : "NO"}`);
  console.log(`  Changed bytes     ${report.changedByteCount} B in ${report.changedRanges.length} ranges`);
  for (const range of report.changedRanges) {
    console.log(`  ${range.offsetHex}  +${range.length}  ${range.fields.join(" + ")}  ${range.before} → ${range.after}`);
  }
}
