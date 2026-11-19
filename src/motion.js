const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

export function canvasToLuma(canvas) {
  const width = canvas.width;
  const height = canvas.height;
  const image = canvas.getContext("2d", { willReadFrequently: true })
    .getImageData(0, 0, width, height);
  const rgba = image.data;
  const gray = new Float32Array(width * height);
  for (let i = 0, p = 0; i < gray.length; i += 1, p += 4) {
    gray[i] = rgba[p] * 0.2126 + rgba[p + 1] * 0.7152 + rgba[p + 2] * 0.0722;
  }
  return { gray, width, height };
}

function blockStatistics(gray, width, cx, cy, blockSize) {
  const half = blockSize / 2;
  const x0 = Math.max(1, Math.floor(cx - half));
  const x1 = Math.min(width - 2, Math.ceil(cx + half));
  const y0 = Math.max(1, Math.floor(cy - half));
  const y1 = Math.min((gray.length / width) - 2, Math.ceil(cy + half));
  let sum = 0;
  let sumSq = 0;
  let gradSum = 0;
  let gxx = 0;
  let gyy = 0;
  let gxy = 0;
  let count = 0;
  for (let y = y0; y <= y1; y += 2) {
    for (let x = x0; x <= x1; x += 2) {
      const index = y * width + x;
      const value = gray[index];
      const gx = (gray[index + 1] - gray[index - 1]) * 0.5;
      const gy = (gray[index + width] - gray[index - width]) * 0.5;
      sum += value;
      sumSq += value * value;
      gradSum += Math.hypot(gx, gy);
      gxx += gx * gx;
      gyy += gy * gy;
      gxy += gx * gy;
      count += 1;
    }
  }
  if (!count) return { variance: 0, meanGradient: 0, minEigen: 0 };
  const mean = sum / count;
  const variance = Math.max(0, sumSq / count - mean * mean);
  const ax = gxx / count;
  const by = gyy / count;
  const cross = gxy / count;
  const root = Math.sqrt(Math.max(0, (ax - by) * (ax - by) + 4 * cross * cross));
  return {
    variance,
    meanGradient: gradSum / count,
    minEigen: Math.max(0, 0.5 * (ax + by - root)),
  };
}

function sampleCost(a, b, width, height, cx, cy, dx, dy, blockSize, step) {
  const half = blockSize / 2;
  const sx0 = Math.round(cx - half);
  const sy0 = Math.round(cy - half);
  const tx0 = sx0 + dx;
  const ty0 = sy0 + dy;
  if (tx0 < 0 || ty0 < 0 || tx0 + blockSize > width || ty0 + blockSize > height) {
    return Number.POSITIVE_INFINITY;
  }
  let cost = 0;
  let count = 0;
  for (let oy = 0; oy < blockSize; oy += step) {
    let ai = (sy0 + oy) * width + sx0;
    let bi = (ty0 + oy) * width + tx0;
    for (let ox = 0; ox < blockSize; ox += step) {
      cost += Math.abs(a[ai + ox] - b[bi + ox]);
      count += 1;
    }
  }
  return cost / count;
}

function searchVector(a, b, width, height, cx, cy, search, blockSize, stats) {
  let bestDx = 0;
  let bestDy = 0;
  let bestCost = Number.POSITIVE_INFINITY;
  let secondCost = Number.POSITIVE_INFINITY;
  const coarseStep = 2;
  for (let dy = -search; dy <= search; dy += coarseStep) {
    for (let dx = -search; dx <= search; dx += coarseStep) {
      const cost = sampleCost(a, b, width, height, cx, cy, dx, dy, blockSize, coarseStep);
      if (cost < bestCost) {
        secondCost = bestCost;
        bestCost = cost;
        bestDx = dx;
        bestDy = dy;
      } else if (cost < secondCost) {
        secondCost = cost;
      }
    }
  }
  for (let dy = bestDy - 1; dy <= bestDy + 1; dy += 1) {
    for (let dx = bestDx - 1; dx <= bestDx + 1; dx += 1) {
      if (dx === bestDx && dy === bestDy) continue;
      const cost = sampleCost(a, b, width, height, cx, cy, dx, dy, blockSize, 1);
      if (cost < bestCost) {
        secondCost = bestCost;
        bestCost = cost;
        bestDx = dx;
        bestDy = dy;
      }
    }
  }
  const refinedCost = sampleCost(a, b, width, height, cx, cy, bestDx, bestDy, blockSize, 1);
  const gap = Number.isFinite(secondCost) ? Math.max(0, secondCost - refinedCost) : 0;
  return {
    dx: bestDx,
    dy: bestDy,
    cost: refinedCost,
    gap,
    texture: stats,
  };
}

function baseGrid(width, height, blockSize, stride) {
  const half = blockSize / 2;
  const xs = [];
  const ys = [];
  for (let x = half; x <= width - half; x += stride) xs.push(Math.round(x));
  for (let y = half; y <= height - half; y += stride) ys.push(Math.round(y));
  return { xs, ys };
}

function estimateOneDirection(a, b, width, height, blockSize, stride, search, grid) {
  const vectors = new Array(grid.xs.length * grid.ys.length);
  let index = 0;
  for (let gy = 0; gy < grid.ys.length; gy += 1) {
    for (let gx = 0; gx < grid.xs.length; gx += 1) {
      const cx = grid.xs[gx];
      const cy = grid.ys[gy];
      const stats = blockStatistics(a, width, cx, cy, blockSize);
      const result = searchVector(a, b, width, height, cx, cy, search, blockSize, stats);
      vectors[index] = {
        index,
        gx,
        gy,
        x: cx,
        y: cy,
        vx: result.dx,
        vy: result.dy,
        cost: result.cost,
        gap: result.gap,
        variance: stats.variance,
        meanGradient: stats.meanGradient,
        minEigen: stats.minEigen,
      };
      index += 1;
    }
  }
  return vectors;
}

function nearestVector(vectors, grid, width, height, blockSize, stride, x, y) {
  const firstX = grid.xs[0];
  const firstY = grid.ys[0];
  const gx = clamp(Math.round((x - firstX) / stride), 0, grid.xs.length - 1);
  const gy = clamp(Math.round((y - firstY) / stride), 0, grid.ys.length - 1);
  const inside = x >= blockSize / 2 && y >= blockSize / 2 && x <= width - blockSize / 2 && y <= height - blockSize / 2;
  if (!inside) return null;
  return vectors[gy * grid.xs.length + gx];
}

function applyConfidence(forward, reverse, grid, width, height, blockSize, stride) {
  const cols = grid.xs.length;
  const rows = grid.ys.length;
  for (const vector of forward) {
    const gradientScore = clamp((vector.meanGradient - 1.8) / 11, 0, 1);
    const eigenScore = clamp((vector.minEigen - 0.65) / 14, 0, 1);
    vector.textureScore = clamp(gradientScore * 0.72 + eigenScore * 0.28, 0, 1);
    vector.matchScore = Number.isFinite(vector.cost)
      ? clamp(1 - vector.cost / 42, 0, 1)
      : 0;
    vector.ambiguityScore = clamp(vector.gap / 4.5, 0, 1);
    vector.fbError = null;
    vector.neighborDiff = 0;
    vector.occlusionWeight = 1;
    vector.confidence = 0;
  }

  for (const vector of forward) {
    const qx = vector.x + vector.vx;
    const qy = vector.y + vector.vy;
    const reverseVector = nearestVector(reverse, grid, width, height, blockSize, stride, qx, qy);
    if (!reverseVector) {
      vector.fbError = 99;
    } else {
      vector.fbError = Math.hypot(vector.vx + reverseVector.vx, vector.vy + reverseVector.vy);
    }
    let diff = 0;
    for (let oy = -1; oy <= 1; oy += 1) {
      for (let ox = -1; ox <= 1; ox += 1) {
        if (!ox && !oy) continue;
        const nx = vector.gx + ox;
        const ny = vector.gy + oy;
        if (nx < 0 || ny < 0 || nx >= cols || ny >= rows) continue;
        const neighbor = forward[ny * cols + nx];
        if (neighbor.textureScore < 0.12 || neighbor.matchScore < 0.1) continue;
        diff = Math.max(diff, Math.hypot(vector.vx - neighbor.vx, vector.vy - neighbor.vy));
      }
    }
    vector.neighborDiff = diff;
  }

  for (const vector of forward) {
    const fbScore = clamp(1 - vector.fbError / 2.4, 0, 1);
    const edgeScore = clamp((vector.neighborDiff - 3) / 12, 0, 1);
    const costEvidence = clamp((vector.cost - 4) / 28, 0, 1);
    const textureEvidence = clamp((vector.textureScore - 0.06) / 0.2, 0, 1);
    const unreliable = (1 - fbScore) * textureEvidence;
    const fbBoundary = unreliable * clamp((vector.cost - 1) / 16, 0, 1);
    const edgeBoundary = edgeScore * costEvidence * textureEvidence;
    vector.boundaryScore = Math.max(fbBoundary, edgeBoundary);
    vector.occlusionWeight = clamp(1 - 0.95 * Math.max(unreliable, vector.boundaryScore), 0.08, 1);
    const matchTerm = 0.18 + 0.82 * vector.matchScore;
    const ambiguityTerm = 0.45 + 0.55 * vector.ambiguityScore;
    vector.confidence = clamp(
      vector.textureScore * matchTerm * ambiguityTerm * vector.occlusionWeight,
      0,
      1,
    );
    vector.occlusionBoundary =
      vector.textureScore > 0.18 &&
      vector.cost > 2.5 &&
      vector.boundaryScore > 0.34 &&
      vector.x > blockSize &&
      vector.x < width - blockSize &&
      vector.y > blockSize &&
      vector.y < height - blockSize &&
      (vector.fbError > 1.35 || vector.neighborDiff > 4);
  }

  return forward;
}

export function estimateMotion(frameA, frameB, options = {}) {
  const blockSize = options.blockSize ?? 24;
  const search = options.search ?? 16;
  const width = frameA.width;
  const height = frameA.height;
  const stride = Math.max(8, Math.round(blockSize / 2));
  const grid = baseGrid(width, height, blockSize, stride);
  const started = performance.now();
  const forward = estimateOneDirection(
    frameA.gray,
    frameB.gray,
    width,
    height,
    blockSize,
    stride,
    search,
    grid,
  );
  const reverse = estimateOneDirection(
    frameB.gray,
    frameA.gray,
    width,
    height,
    blockSize,
    stride,
    search,
    grid,
  );
  applyConfidence(forward, reverse, grid, width, height, blockSize, stride);
  return {
    width,
    height,
    blockSize,
    stride,
    search,
    grid,
    vectors: forward,
    reverseVectors: reverse,
    computeMs: performance.now() - started,
  };
}

export function sampleField(field, x, y) {
  const { grid, vectors, stride } = field;
  const gx = Math.round((x - grid.xs[0]) / stride);
  const gy = Math.round((y - grid.ys[0]) / stride);
  if (gx < 0 || gy < 0 || gx >= grid.xs.length || gy >= grid.ys.length) return null;
  return vectors[gy * grid.xs.length + gx];
}

