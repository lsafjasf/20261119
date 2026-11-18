#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { checkGif } = require('./checker');

function usage() {
  return [
    'Usage: node src/cli.js <animation.gif> [options]',
    '',
    'Options:',
    '  --rounds <n>       Number of playback loops to simulate (default: 2)',
    '  --out <dir>        Artifact directory (default: artifacts)',
    '  --allow-residue    Exit 0 when loop residue is the only failure',
    '  -h, --help         Show this help',
  ].join('\n');
}

function parseArguments(argv) {
  const options = {
    rounds: 2,
    outDir: 'artifacts',
    allowResidue: false,
  };
  const positional = [];
  for (let i = 0; i < argv.length; i += 1) {
    const argument = argv[i];
    if (argument === '-h' || argument === '--help') {
      options.help = true;
    } else if (argument === '--rounds') {
      i += 1;
      options.rounds = Number(argv[i]);
    } else if (argument === '--out') {
      i += 1;
      options.outDir = argv[i];
    } else if (argument === '--allow-residue') {
      options.allowResidue = true;
    } else if (argument.startsWith('-')) {
      throw new Error(`unknown option ${argument}`);
    } else {
      positional.push(argument);
    }
  }
  if (positional.length > 1) {
    throw new Error('expected exactly one GIF path');
  }
  if (!Number.isInteger(options.rounds) || options.rounds < 1) {
    throw new Error('--rounds must be a positive integer');
  }
  options.sourcePath = positional[0];
  return options;
}

function main() {
  const options = parseArguments(process.argv.slice(2));
  if (options.help) {
    process.stdout.write(`${usage()}\n`);
    return;
  }
  if (!options.sourcePath) {
    throw new Error('missing GIF path');
  }

  const buffer = fs.readFileSync(path.resolve(options.sourcePath));
  const result = checkGif(buffer, options);
  process.stdout.write(result.reportText);

  const restorePassed = result.restorePrevious.every(
    (check) => check.matchesCanvasBeforeFrame,
  ) && result.restoreBackground.every(
    (check) => check.rectangleMatchesBackground && check.outsideRectangleUnchanged,
  );
  const hardFailure = !result.crossResult.ok || !restorePassed;
  if (hardFailure || (!result.roundCheck.passed && !options.allowResidue)) {
    process.exitCode = 2;
  }
}

try {
  main();
} catch (error) {
  process.stderr.write(`${error.message}\n\n${usage()}\n`);
  process.exitCode = 1;
}
