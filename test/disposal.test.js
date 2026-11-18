'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { checkGif } = require('../src/checker');
const { compareRounds, runAnimation } = require('../src/compositor');
const { parseGif } = require('../src/gif');
const { crossCheck } = require('../src/independent-check');
const { buildCorrectSample, buildLeakySample } = require('../src/samples');

function pixel(canvas, x, y, width) {
  const offset = (y * width + x) * 4;
  return Array.from(canvas.slice(offset, offset + 4));
}

test('decodes frame rectangles, transparency and all disposal methods', () => {
  const buffer = buildCorrectSample();
  const gif = parseGif(buffer);

  assert.equal(gif.width, 16);
  assert.equal(gif.height, 16);
  assert.equal(gif.backgroundIndex, 0);
  assert.equal(gif.loopCount, 0);
  assert.deepEqual(gif.frames.map((frame) => frame.disposal), [1, 1, 3, 2, 3, 1, 2]);
  assert.deepEqual(
    gif.frames.map((frame) => frame.transparentIndex),
    [null, 7, 7, null, 7, 7, null],
  );
  assert.deepEqual(
    gif.frames.slice(1, 5).map((frame) => [
      frame.left,
      frame.top,
      frame.width,
      frame.height,
    ]),
    [
      [2, 3, 6, 6],
      [5, 5, 6, 6],
      [9, 2, 4, 4],
      [7, 3, 6, 5],
    ],
  );
});

test('independent decoder matches local pixels and every cumulative canvas', () => {
  const buffer = buildCorrectSample();
  const gif = parseGif(buffer);
  const animation = runAnimation(gif, { rounds: 2 });
  const result = crossCheck(gif, buffer, animation.steps);

  assert.equal(result.ok, true, result.errors.join('\n'));
  assert.equal(result.framesCompared, 7);
  assert.equal(result.localPixelsCompared, 650);
  assert.equal(result.canvasStatesCompared, 28);
});

test('disposal 3 restores the canvas from before the current frame', () => {
  const gif = parseGif(buildCorrectSample());
  const steps = runAnimation(gif, { rounds: 2 }).steps;
  const previousBackgroundFrame = steps[3];
  const restorePreviousFrame = steps[4];
  const background = [16, 24, 32, 255];

  assert.deepEqual(
    pixel(previousBackgroundFrame.drawn, 9, 3, gif.width),
    [67, 97, 238, 255],
  );
  assert.deepEqual(
    pixel(restorePreviousFrame.drawn, 9, 3, gif.width),
    [95, 191, 119, 255],
  );
  assert.deepEqual(
    pixel(restorePreviousFrame.disposed, 9, 3, gif.width),
    background,
  );
  assert.deepEqual(restorePreviousFrame.disposed, restorePreviousFrame.beforeFrame);
  assert.notDeepEqual(restorePreviousFrame.disposed, restorePreviousFrame.drawn);
});

test('transparent pixels leave the accumulated canvas untouched', () => {
  const gif = parseGif(buildCorrectSample());
  const steps = runAnimation(gif, { rounds: 1 }).steps;
  const frame = steps[1];
  const holeColor = pixel(frame.beforeFrame, 4, 4, gif.width);

  assert.deepEqual(pixel(frame.drawn, 4, 4, gif.width), holeColor);
  assert.deepEqual(pixel(frame.drawn, 2, 3, gif.width), [230, 57, 70, 255]);
});

test('loop check catches the leaky fixture but accepts the clean fixture', () => {
  const cleanGif = parseGif(buildCorrectSample());
  const clean = compareRounds(cleanGif, runAnimation(cleanGif, { rounds: 2 }).steps);
  assert.equal(clean.passed, true);

  const leakyGif = parseGif(buildLeakySample());
  const leaky = compareRounds(leakyGif, runAnimation(leakyGif, { rounds: 2 }).steps);
  assert.equal(leaky.passed, false);
  const changed = leaky.comparisons.filter(
    (comparison) => !comparison.drawn.equal || !comparison.disposed.equal,
  );
  assert.equal(changed.length, 3);
  assert.equal(changed[0].drawn.differences, 28);
});

test('checker exports each step, contact tables and reports', () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'gif-disposal-'));
  try {
    const sourcePath = path.join(temporary, 'sample.gif');
    const buffer = buildCorrectSample();
    fs.writeFileSync(sourcePath, buffer);
    const result = checkGif(buffer, {
      sourcePath,
      outDir: path.join(temporary, 'artifacts'),
      rounds: 2,
    });

    assert.equal(result.passed, true);
    assert.equal(result.animation.steps.length, 14);
    for (const filePath of result.stepArtifacts.flatMap((entry) => [
      entry.drawnPath,
      entry.disposedPath,
    ])) {
      assert.equal(fs.readFileSync(filePath).subarray(1, 4).toString('ascii'), 'PNG');
    }
    assert.equal(result.stepArtifacts.length * 2, 28);
    assert.match(result.reportText, /循环残留检查: 通过/);
    assert.match(result.reportText, /独立解码交叉核对: 通过/);
    assert.match(result.reportText, /恢复前一画布 帧4/);

    for (const filePath of [
      result.contact.tableCsv,
      result.contact.framesCsv,
      result.contact.tablePng,
      result.contact.countPng,
      result.reportPath,
      result.jsonPath,
    ]) {
      assert.equal(fs.existsSync(filePath), true, filePath);
    }
    const rows = fs.readFileSync(result.contact.tableCsv, 'utf8').trim().split('\n');
    assert.equal(rows.length, 16);
    assert.equal(rows[0].split(',').length, 16);
    assert.equal(JSON.parse(fs.readFileSync(result.jsonPath, 'utf8')).passed, true);
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
});
