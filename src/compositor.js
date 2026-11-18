'use strict';

const { backgroundRgba, paletteColor } = require('./gif');

const DISPOSAL_INFO = {
  0: {
    code: 0,
    label: '未指定（按保留处理）',
    detail: '未指定清理动作；解码器通常保留当前帧像素',
  },
  1: {
    code: 1,
    label: '保留',
    detail: '绘制后保留当前帧像素，矩形区域不清理',
  },
  2: {
    code: 2,
    label: '恢复背景',
    detail: '把当前帧矩形恢复为逻辑屏幕背景色，矩形外保持不变',
  },
  3: {
    code: 3,
    label: '恢复前一画布',
    detail: '恢复当前帧开始绘制前的整张累计画布，不是上一帧局部 patch',
  },
};

function disposalInfo(method) {
  return DISPOSAL_INFO[method] || {
    code: method,
    label: '保留（保留位处置方式）',
    detail: '保留位处置值，按保留当前像素处理',
  };
}

function clippedBounds(width, height, frame) {
  return {
    x0: Math.max(0, frame.left),
    y0: Math.max(0, frame.top),
    x1: Math.min(width, frame.left + frame.width),
    y1: Math.min(height, frame.top + frame.height),
  };
}

function createCanvas(gif) {
  const pixels = new Uint8Array(gif.width * gif.height * 4);
  const background = backgroundRgba(gif);
  for (let offset = 0; offset < pixels.length; offset += 4) {
    pixels[offset] = background[0];
    pixels[offset + 1] = background[1];
    pixels[offset + 2] = background[2];
    pixels[offset + 3] = background[3];
  }
  return { width: gif.width, height: gif.height, pixels };
}

function cloneCanvas(canvas) {
  return {
    width: canvas.width,
    height: canvas.height,
    pixels: new Uint8Array(canvas.pixels),
  };
}

function paintFrame(canvas, frame, onPaint = null) {
  const bounds = clippedBounds(canvas.width, canvas.height, frame);
  for (let y = bounds.y0; y < bounds.y1; y += 1) {
    for (let x = bounds.x0; x < bounds.x1; x += 1) {
      const localX = x - frame.left;
      const localY = y - frame.top;
      const index = frame.indices[localY * frame.width + localX];
      if (frame.transparentIndex !== null && index === frame.transparentIndex) {
        continue;
      }
      const color = paletteColor(frame.palette, index);
      const offset = (y * canvas.width + x) * 4;
      canvas.pixels[offset] = color[0];
      canvas.pixels[offset + 1] = color[1];
      canvas.pixels[offset + 2] = color[2];
      canvas.pixels[offset + 3] = color[3];
      if (onPaint) {
        onPaint(y * canvas.width + x, color);
      }
    }
  }
}

function restoreRectangle(canvas, frame, background) {
  const bounds = clippedBounds(canvas.width, canvas.height, frame);
  for (let y = bounds.y0; y < bounds.y1; y += 1) {
    for (let x = bounds.x0; x < bounds.x1; x += 1) {
      const offset = (y * canvas.width + x) * 4;
      canvas.pixels[offset] = background[0];
      canvas.pixels[offset + 1] = background[1];
      canvas.pixels[offset + 2] = background[2];
      canvas.pixels[offset + 3] = background[3];
    }
  }
}

function disposeFrame(canvas, beforeFrame, frame, gif) {
  if (frame.disposal === 2) {
    restoreRectangle(canvas, frame, backgroundRgba(gif));
  } else if (frame.disposal === 3) {
    canvas.pixels.set(beforeFrame.pixels);
  }
}

function createPaintLedger(gif) {
  const pixelCount = gif.width * gif.height;
  return {
    lastFrame: new Int32Array(pixelCount).fill(-1),
    frameSets: Array.from({ length: pixelCount }, () => new Set()),
    counts: new Uint16Array(pixelCount),
  };
}

function recordPaint(ledger, pixelIndex, frameIndex) {
  ledger.lastFrame[pixelIndex] = frameIndex;
  if (!ledger.frameSets[pixelIndex].has(frameIndex)) {
    ledger.frameSets[pixelIndex].add(frameIndex);
    ledger.counts[pixelIndex] += 1;
  }
}

function runAnimation(gif, options = {}) {
  const rounds = options.rounds === undefined ? 2 : options.rounds;
  if (!Number.isInteger(rounds) || rounds < 1) {
    throw new Error('rounds must be a positive integer');
  }

  const canvas = createCanvas(gif);
  const ledger = createPaintLedger(gif);
  const steps = [];
  const initialCanvas = cloneCanvas(canvas);

  for (let round = 0; round < rounds; round += 1) {
    for (const frame of gif.frames) {
      const beforeFrame = cloneCanvas(canvas);
      const drawn = cloneCanvas(canvas);
      paintFrame(drawn, frame, (pixelIndex) => {
        if (round === 0) {
          recordPaint(ledger, pixelIndex, frame.index);
        }
      });

      const disposed = cloneCanvas(drawn);
      disposeFrame(disposed, beforeFrame, frame, gif);
      canvas.pixels.set(disposed.pixels);

      steps.push({
        round,
        frameIndex: frame.index,
        frame,
        beforeFrame: beforeFrame.pixels,
        drawn: drawn.pixels,
        disposed: disposed.pixels,
      });
    }
  }

  return {
    initialCanvas: initialCanvas.pixels,
    finalCanvas: new Uint8Array(canvas.pixels),
    steps,
    ledger,
  };
}

function compareCanvases(width, height, left, right) {
  let differences = 0;
  let firstDifference = null;
  for (let pixelIndex = 0; pixelIndex < width * height; pixelIndex += 1) {
    const offset = pixelIndex * 4;
    if (
      left[offset] !== right[offset]
      || left[offset + 1] !== right[offset + 1]
      || left[offset + 2] !== right[offset + 2]
      || left[offset + 3] !== right[offset + 3]
    ) {
      differences += 1;
      if (!firstDifference) {
        firstDifference = {
          x: pixelIndex % width,
          y: Math.floor(pixelIndex / width),
          left: Array.from(left.subarray(offset, offset + 4)),
          right: Array.from(right.subarray(offset, offset + 4)),
        };
      }
    }
  }
  return { equal: differences === 0, differences, firstDifference };
}

function compareRounds(gif, steps) {
  const rounds = Math.max(0, ...steps.map((step) => step.round)) + 1;
  const result = { rounds, passed: true, comparisons: [] };
  if (rounds < 2) {
    return result;
  }

  const firstRound = steps.filter((step) => step.round === 0);
  for (let round = 1; round < rounds; round += 1) {
    for (const firstStep of firstRound) {
      const step = steps.find(
        (candidate) => candidate.round === round
          && candidate.frameIndex === firstStep.frameIndex,
      );
      if (!step) {
        continue;
      }
      const drawn = compareCanvases(gif.width, gif.height, firstStep.drawn, step.drawn);
      const disposed = compareCanvases(
        gif.width,
        gif.height,
        firstStep.disposed,
        step.disposed,
      );
      if (!drawn.equal || !disposed.equal) {
        result.passed = false;
      }
      result.comparisons.push({
        round,
        frameIndex: firstStep.frameIndex,
        drawn,
        disposed,
      });
    }
  }
  return result;
}

module.exports = {
  cloneCanvas,
  compareCanvases,
  compareRounds,
  createCanvas,
  disposalInfo,
  paintFrame,
  runAnimation,
};
