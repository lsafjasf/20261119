import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildSampleSet } from "../src/sample-builder.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outputDir = path.join(root, "samples");
const base = new Uint8Array(fs.readFileSync(path.join(root, "assets", "base.jpg")));
const samples = buildSampleSet(base);
fs.mkdirSync(outputDir, { recursive: true });

const index = samples.map(({ bytes, ...metadata }, order) => {
  fs.writeFileSync(path.join(outputDir, metadata.fileName), bytes);
  return { ...metadata, order };
});
fs.writeFileSync(path.join(outputDir, "index.json"), `${JSON.stringify(index, null, 2)}\n`);
console.log(`Generated ${index.length} samples in ${path.relative(process.cwd(), outputDir)}/`);
