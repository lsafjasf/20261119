import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { runDemo } from "../src/pipeline.js";
import { renderReport, summarizeForConsole } from "../src/report.js";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const artifacts = join(root, "artifacts");
const data = runDemo();
const html = renderReport(data);

await mkdir(artifacts, { recursive: true });
await Promise.all([
  writeFile(join(artifacts, "lifecycle-data.json"), `${JSON.stringify(data, null, 2)}\n`),
  writeFile(join(artifacts, "lifecycle-report.html"), html),
]);

console.log(summarizeForConsole(data));
console.log(`\nWrote ${join(artifacts, "lifecycle-report.html")}`);
console.log(`Wrote ${join(artifacts, "lifecycle-data.json")}`);

if (!data.summary.allClean) {
  process.exitCode = 1;
}
