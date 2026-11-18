#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { checkGif } = require('../src/checker');
const { buildCorrectSample, buildLeakySample } = require('../src/samples');

const outDir = path.resolve('artifacts');
fs.mkdirSync(outDir, { recursive: true });

const fixtures = [
  {
    name: 'sample',
    buffer: buildCorrectSample(),
    expectPass: true,
  },
  {
    name: 'sample_leaky',
    buffer: buildLeakySample(),
    expectPass: false,
  },
];

let unexpected = false;
for (const fixture of fixtures) {
  const gifPath = path.join(outDir, `${fixture.name}.gif`);
  fs.writeFileSync(gifPath, fixture.buffer);
  const result = checkGif(fixture.buffer, {
    sourcePath: gifPath,
    outDir,
    rounds: 2,
  });
  process.stdout.write(result.reportText);
  process.stdout.write('\n');
  if (result.passed !== fixture.expectPass) {
    unexpected = true;
    process.stderr.write(
      `${fixture.name}: expected ${fixture.expectPass ? 'pass' : 'failure'}, `
      + `got ${result.passed ? 'pass' : 'failure'}\n`,
    );
  }
}

if (unexpected) {
  process.exitCode = 1;
}
