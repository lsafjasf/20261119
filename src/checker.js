'use strict';

const fs = require('node:fs');
const path = require('node:path');
const {
  compareCanvases,
  compareRounds,
  disposalInfo,
  runAnimation,
} = require('./compositor');
const { paletteColor, parseGif } = require('./gif');
const { crossCheck } = require('./independent-check');
const {
  ensureDirectory,
  relativePath,
  saveCanvasPng,
  writeContactArtifacts,
} = require('./render');

function loopDescription(loopCount) {
  if (loopCount === null) return '未声明循环';
  if (loopCount === 0) return '无限';
  return `${loopCount} 次`;
}

function transparentDescription(index) {
  return index === null ? 'None' : String(index);
}

function previousFramePatchMismatches(gif, step) {
  if (step.frameIndex === 0) {
    return null;
  }
  const previousFrame = gif.frames[step.frameIndex - 1];
  const frame = step.frame;
  const x0 = Math.max(frame.left, previousFrame.left);
  const y0 = Math.max(frame.top, previousFrame.top);
  const x1 = Math.min(frame.left + frame.width, previousFrame.left + previousFrame.width);
  const y1 = Math.min(frame.top + frame.height, previousFrame.top + previousFrame.height);
  let overlappingPixels = 0;
  let differingPixels = 0;

  for (let y = y0; y < y1; y += 1) {
    for (let x = x0; x < x1; x += 1) {
      const previousIndex = previousFrame.indices[
        (y - previousFrame.top) * previousFrame.width + (x - previousFrame.left)
      ];
      if (
        previousFrame.transparentIndex !== null
        && previousIndex === previousFrame.transparentIndex
      ) {
        continue;
      }
      const previousColor = paletteColor(previousFrame.palette, previousIndex);
      const disposedOffset = (y * gif.width + x) * 4;
      overlappingPixels += 1;
      if (
        step.disposed[disposedOffset] !== previousColor[0]
        || step.disposed[disposedOffset + 1] !== previousColor[1]
        || step.disposed[disposedOffset + 2] !== previousColor[2]
        || step.disposed[disposedOffset + 3] !== previousColor[3]
      ) {
        differingPixels += 1;
      }
    }
  }
  return { overlappingPixels, differingPixels };
}

function analyzeRestorePrevious(gif, steps) {
  return steps
    .filter((step) => step.round === 0 && step.frame.disposal === 3)
    .map((step) => {
      const beforeComparison = compareCanvases(
        gif.width,
        gif.height,
        step.beforeFrame,
        step.disposed,
      );
      return {
        frameIndex: step.frameIndex,
        matchesCanvasBeforeFrame: beforeComparison.equal,
        previousFramePatch: previousFramePatchMismatches(gif, step),
      };
    });
}

function analyzeRestoreBackground(gif, steps) {
  const background = paletteColor(gif.globalColorTable, gif.backgroundIndex);
  return steps
    .filter((step) => step.round === 0 && step.frame.disposal === 2)
    .map((step) => {
      const frame = step.frame;
      let mismatches = 0;
      let outsideChanges = 0;
      for (let y = 0; y < gif.height; y += 1) {
        for (let x = 0; x < gif.width; x += 1) {
          const inside = x >= frame.left && x < frame.left + frame.width
            && y >= frame.top && y < frame.top + frame.height;
          const offset = (y * gif.width + x) * 4;
          if (inside) {
            if (
              step.disposed[offset] !== background[0]
              || step.disposed[offset + 1] !== background[1]
              || step.disposed[offset + 2] !== background[2]
              || step.disposed[offset + 3] !== background[3]
            ) {
              mismatches += 1;
            }
          } else if (
            step.disposed[offset] !== step.drawn[offset]
            || step.disposed[offset + 1] !== step.drawn[offset + 1]
            || step.disposed[offset + 2] !== step.drawn[offset + 2]
            || step.disposed[offset + 3] !== step.drawn[offset + 3]
          ) {
            outsideChanges += 1;
          }
        }
      }
      return {
        frameIndex: step.frameIndex,
        rectangleMatchesBackground: mismatches === 0,
        outsideRectangleUnchanged: outsideChanges === 0,
      };
    });
}

function roundLeadIn(round, frameIndex) {
  return `[第${round + 1}轮 帧${frameIndex}]`;
}

function frameReportLine(step, drawnPath, disposedPath) {
  const frame = step.frame;
  const info = disposalInfo(frame.disposal);
  return [
    `${roundLeadIn(step.round, step.frameIndex)} `
      + `帧矩形=(${frame.left},${frame.top},${frame.width}x${frame.height}) `
      + `透明索引=${transparentDescription(frame.transparentIndex)} `
      + `处置方式=${frame.disposal} (${info.label})`,
    `    说明: ${info.detail}; 延迟=${frame.delay * 10}ms`,
    `    绘制后累计画布: ${relativePath(drawnPath)}`,
    `    处置后累计画布: ${relativePath(disposedPath)}`,
  ].join('\n');
}

function restoreReportLines(restorePrevious, restoreBackground) {
  const lines = ['关键语义检查:'];
  for (const check of restorePrevious) {
    const previous = check.previousFramePatch;
    const previousText = previous
      ? `与前一张局部帧重叠的 ${previous.overlappingPixels} 像素中 `
        + `${previous.differingPixels} 个不同`
      : '没有上一帧可比';
    lines.push(
      `  恢复前一画布 帧${check.frameIndex}: `
      + `处置后==帧开始前累计画布 ${check.matchesCanvasBeforeFrame ? '通过' : '失败'}; `
      + `${previousText}`
      + (previous && previous.differingPixels > 0 ? ' (未误用上一局部帧)' : ''),
    );
  }
  for (const check of restoreBackground) {
    lines.push(
      `  恢复背景 帧${check.frameIndex}: `
      + `矩形内全为背景色 ${check.rectangleMatchesBackground ? '通过' : '失败'}; `
      + `矩形外保持不变 ${check.outsideRectangleUnchanged ? '通过' : '失败'}`,
    );
  }
  return lines;
}

function residueReportLines(roundCheck) {
  if (roundCheck.rounds < 2) {
    return ['循环残留检查: 未模拟多轮'];
  }
  if (roundCheck.passed) {
    const lastRound = Math.max(...roundCheck.comparisons.map((entry) => entry.round)) + 1;
    return [
      `循环残留检查: 通过, 第 ${lastRound} 轮与第 1 轮逐步画布完全一致, 无遗留`,
    ];
  }
  const lines = ['循环残留检查: 失败! 发现上一轮内容遗留'];
  for (const comparison of roundCheck.comparisons) {
    if (comparison.drawn.equal && comparison.disposed.equal) {
      continue;
    }
    const drawnDetail = comparison.drawn.firstDifference
      ? ` 首处=(${comparison.drawn.firstDifference.x},${comparison.drawn.firstDifference.y})`
      : '';
    lines.push(
      `  第 ${comparison.round + 1} 轮 帧 ${comparison.frameIndex}: 绘制后差异 `
      + `${comparison.drawn.differences} 像素, 处置后差异 `
      + `${comparison.disposed.differences} 像素${drawnDetail}`,
    );
  }
  return lines;
}

function crossCheckLine(result) {
  if (result.ok) {
    return `独立解码交叉核对: 通过 (${result.library}@${result.version}, `
      + `${result.framesCompared} 帧 / ${result.localPixelsCompared} 局部像素, `
      + `${result.canvasStatesCompared} 个累计画布快照)`;
  }
  return `独立解码交叉核对: 失败\n${result.errors
    .slice(0, 20)
    .map((error) => `  - ${error}`)
    .join('\n')}`;
}

function contactLine(gif, ledger) {
  let paintedPixels = 0;
  for (const count of ledger.counts) {
    if (count > 0) paintedPixels += 1;
  }
  return `接触统计: ${paintedPixels}/${gif.width * gif.height} 像素被至少一帧绘制过`;
}

function writeStepImages(outDir, baseName, gif, steps) {
  const stepsDir = path.join(outDir, 'steps');
  ensureDirectory(stepsDir);
  const artifacts = [];
  for (const step of steps) {
    const stem = `${baseName}_r${step.round}_f${step.frameIndex}`;
    const drawnPath = path.join(stepsDir, `${stem}_drawn.png`);
    const disposedPath = path.join(stepsDir, `${stem}_disposed.png`);
    saveCanvasPng(drawnPath, gif.width, gif.height, step.drawn);
    saveCanvasPng(disposedPath, gif.width, gif.height, step.disposed);
    artifacts.push({ drawnPath, disposedPath });
  }
  return artifacts;
}

function buildReport(options) {
  const {
    sourcePath,
    gif,
    rounds,
    crossResult,
    roundCheck,
    restorePrevious,
    restoreBackground,
    stepArtifacts,
    contact,
    ledger,
  } = options;
  const lines = [
    `GIF 处置语义检查报告: ${relativePath(sourcePath)}`,
    `逻辑屏幕 ${gif.width}x${gif.height}, 背景色索引 ${gif.backgroundIndex}, `
      + `NETSCAPE 循环=${loopDescription(gif.loopCount)} (模拟 ${rounds} 轮)`,
    crossCheckLine(crossResult),
    '',
  ];

  for (let round = 0; round < rounds; round += 1) {
    gif.frames.forEach((frame, frameIndex) => {
      const stepIndex = round * gif.frames.length + frameIndex;
      const paths = stepArtifacts[stepIndex];
      lines.push(
        frameReportLine(
          { round, frameIndex, frame },
          paths.drawnPath,
          paths.disposedPath,
        ),
      );
    });
  }

  lines.push('');
  lines.push(...restoreReportLines(restorePrevious, restoreBackground));
  lines.push(...residueReportLines(roundCheck));
  lines.push(
    `最终接触表: ${relativePath(contact.tableCsv)} / `
    + `${relativePath(contact.tablePng)}, ${relativePath(contact.framesCsv)}, `
    + `${relativePath(contact.countPng)}`,
  );
  lines.push(contactLine(gif, ledger));
  const passed = crossResult.ok
    && roundCheck.passed
    && restorePrevious.every((check) => check.matchesCanvasBeforeFrame)
    && restoreBackground.every(
      (check) => check.rectangleMatchesBackground && check.outsideRectangleUnchanged,
    );
  lines.push(`结论: ${passed ? '通过' : '失败'}`);
  return { text: `${lines.join('\n')}\n`, passed };
}

function checkGif(buffer, options) {
  const sourcePath = path.resolve(options.sourcePath);
  const outDir = path.resolve(options.outDir || 'artifacts');
  const rounds = options.rounds === undefined ? 2 : options.rounds;
  const baseName = path.parse(sourcePath).name;
  ensureDirectory(outDir);

  const gif = parseGif(buffer);
  const animation = runAnimation(gif, { rounds });
  const crossResult = crossCheck(gif, buffer, animation.steps);
  const roundCheck = compareRounds(gif, animation.steps);
  const restorePrevious = analyzeRestorePrevious(gif, animation.steps);
  const restoreBackground = analyzeRestoreBackground(gif, animation.steps);
  const stepArtifacts = writeStepImages(
    outDir,
    baseName,
    gif,
    animation.steps,
  );
  const contact = writeContactArtifacts(
    outDir,
    baseName,
    gif,
    animation.ledger,
    '最终接触表',
  );
  const report = buildReport({
    sourcePath,
    gif,
    rounds,
    crossResult,
    roundCheck,
    restorePrevious,
    restoreBackground,
    stepArtifacts,
    contact,
    ledger: animation.ledger,
  });

  const reportPath = path.join(outDir, `report_${baseName}.txt`);
  const jsonPath = path.join(outDir, `report_${baseName}.json`);
  fs.writeFileSync(reportPath, report.text);
  fs.writeFileSync(jsonPath, `${JSON.stringify({
    source: relativePath(sourcePath),
    logicalScreen: { width: gif.width, height: gif.height },
    backgroundIndex: gif.backgroundIndex,
    loopCount: gif.loopCount,
    rounds,
    frames: gif.frames.map((frame) => ({
      index: frame.index,
      rectangle: {
        left: frame.left,
        top: frame.top,
        width: frame.width,
        height: frame.height,
      },
      transparentIndex: frame.transparentIndex,
      disposal: frame.disposal,
      delayMs: frame.delay * 10,
    })),
    independentCrossCheck: crossResult,
    loopResidue: {
      passed: roundCheck.passed,
      comparisons: roundCheck.comparisons.map((comparison) => ({
        round: comparison.round,
        frameIndex: comparison.frameIndex,
        drawnDifferences: comparison.drawn.differences,
        disposedDifferences: comparison.disposed.differences,
      })),
    },
    restorePrevious,
    restoreBackground,
    contact: {
      tableCsv: relativePath(contact.tableCsv),
      framesCsv: relativePath(contact.framesCsv),
      tablePng: relativePath(contact.tablePng),
      countPng: relativePath(contact.countPng),
    },
    report: relativePath(reportPath),
    passed: report.passed,
  }, null, 2)}\n`);

  return {
    passed: report.passed,
    gif,
    animation,
    crossResult,
    roundCheck,
    restorePrevious,
    restoreBackground,
    reportText: report.text,
    reportPath,
    jsonPath,
    stepArtifacts,
    contact,
  };
}

module.exports = {
  checkGif,
};
