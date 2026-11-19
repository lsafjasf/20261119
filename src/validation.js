const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

function rectDistanceToPoint(rect, x, y) {
  const dx = Math.max(rect.x - x, 0, x - (rect.x + rect.w));
  const dy = Math.max(rect.y - y, 0, y - (rect.y + rect.h));
  return Math.hypot(dx, dy);
}

function objectEdgeDistance(rect, x, y) {
  const inside = x >= rect.x && x <= rect.x + rect.w &&
    y >= rect.y && y <= rect.y + rect.h;
  if (!inside) return rectDistanceToPoint(rect, x, y);
  return Math.min(x - rect.x, rect.x + rect.w - x, y - rect.y, rect.y + rect.h - y);
}

function groundTruthVector(scene, x, y, halfBlock) {
  const gt = scene.gt;
  if (!gt) return { vx: null, vy: null, valid: false, occluded: false };
  if (gt.kind === "translation") {
    return { vx: gt.dx, vy: gt.dy, valid: true, occluded: false };
  }
  if (gt.kind === "rotation") {
    const radians = (gt.angle * Math.PI) / 180;
    const cos = Math.cos(radians);
    const sin = Math.sin(radians);
    const px = x - gt.cx;
    const py = y - gt.cy;
    return {
      vx: gt.cx + cos * px - sin * py - x,
      vy: gt.cy + sin * px + cos * py - y,
      valid: true,
      occluded: false,
    };
  }
  if (gt.kind === "occlusion") {
    const inObjectA = x >= gt.objectA.x && x <= gt.objectA.x + gt.objectA.w &&
      y >= gt.objectA.y && y <= gt.objectA.y + gt.objectA.h;
    const nearObjectA = objectEdgeDistance(gt.objectA, x, y) < halfBlock;
    if (inObjectA) {
      return {
        vx: gt.fg.dx,
        vy: gt.fg.dy,
        valid: !nearObjectA,
        occluded: false,
        region: "foreground",
      };
    }
    const qx = x + gt.bg.dx;
    const qy = y + gt.bg.dy;
    const occluded = qx >= gt.objectB.x - halfBlock &&
      qx <= gt.objectB.x + gt.objectB.w + halfBlock &&
      qy >= gt.objectB.y - halfBlock &&
      qy <= gt.objectB.y + gt.objectB.h + halfBlock;
    return {
      vx: gt.bg.dx,
      vy: gt.bg.dy,
      valid: !occluded && !nearObjectA,
      occluded,
      region: "background",
    };
  }
  return { vx: null, vy: null, valid: false, occluded: false };
}

export function attachGroundTruth(scene, field, threshold = 0.42) {
  const half = field.blockSize / 2;
  let gtVectors = 0;
  for (const vector of field.vectors) {
    const gt = groundTruthVector(scene, vector.x, vector.y, half);
    vector.gtVx = gt.vx;
    vector.gtVy = gt.vy;
    vector.gtValid = gt.valid &&
      vector.x + gt.vx >= half &&
      vector.x + gt.vx <= field.width - half &&
      vector.y + gt.vy >= half &&
      vector.y + gt.vy <= field.height - half;
    vector.gtOccluded = gt.occluded;
    vector.gtRegion = gt.region ?? null;
    vector.epe = vector.gtValid
      ? Math.hypot(vector.vx - gt.vx, vector.vy - gt.vy)
      : null;
    if (vector.gtValid) gtVectors += 1;
  }
  return {
    available: Boolean(scene.gt),
    kind: scene.gt?.kind ?? "custom",
    gtVectors,
    threshold,
  };
}

export function computeMetrics(field, validation, threshold = 0.42) {
  const vectors = field.vectors;
  const valid = vectors.filter((vector) => vector.confidence >= threshold && vector.textureScore > 0.08);
  const epeRows = vectors.filter((vector) => vector.gtValid && Number.isFinite(vector.epe));
  const highConfidenceEpe = epeRows.filter((vector) => vector.confidence >= threshold);
  const weightedBase = highConfidenceEpe.length ? highConfidenceEpe : epeRows;
  let weightedTotal = 0;
  let weightSum = 0;
  for (const vector of weightedBase) {
    const weight = Math.max(0.02, vector.confidence * vector.occlusionWeight);
    weightedTotal += vector.epe * weight;
    weightSum += weight;
  }
  const meanEpe = weightSum ? weightedTotal / weightSum : null;
  const sorted = weightedBase.map((vector) => vector.epe).sort((a, b) => a - b);
  const p95 = sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] : null;
  const accurate = sorted.filter((value) => value < 1).length;
  const accuracyRate = sorted.length ? accurate / sorted.length : null;
  const allSorted = epeRows.map((vector) => vector.epe).sort((a, b) => a - b);
  const allP95 = allSorted.length
    ? allSorted[Math.min(allSorted.length - 1, Math.floor(allSorted.length * 0.95))]
    : null;
  const allAccuracyRate = allSorted.length
    ? allSorted.filter((value) => value < 1).length / allSorted.length
    : null;

  let directionSin = 0;
  let directionCos = 0;
  let directionWeight = 0;
  let magnitudeSum = 0;
  let magnitudeWeight = 0;
  for (const vector of valid) {
    const radians = Math.atan2(vector.vy, vector.vx);
    const weight = vector.confidence;
    directionSin += Math.sin(radians) * weight;
    directionCos += Math.cos(radians) * weight;
    directionWeight += weight;
    magnitudeSum += Math.hypot(vector.vx, vector.vy) * weight;
    magnitudeWeight += weight;
  }
  let dominantAngle = null;
  let directionCoherence = null;
  if (directionSin !== 0 || directionCos !== 0) {
    dominantAngle = (Math.atan2(directionSin, directionCos) * 180 / Math.PI + 360) % 360;
    directionCoherence = directionWeight
      ? Math.hypot(directionSin, directionCos) / directionWeight
      : 0;
  }

  const bins = [0, 0, 0, 0, 0, 0, 0, 0];
  for (const value of sorted) {
    const index = value >= 4 ? bins.length - 1 : Math.floor(value / 0.5);
    bins[Math.min(index, bins.length - 1)] += 1;
  }

  return {
    total: vectors.length,
    valid: valid.length,
    validRatio: vectors.length ? valid.length / vectors.length : 0,
    meanEpe,
    p95,
    accuracyRate,
    epeSamples: sorted.length,
    allEpeSamples: allSorted.length,
    allP95,
    allAccuracyRate,
    epeHistogram: bins,
    meanMagnitude: magnitudeWeight ? magnitudeSum / magnitudeWeight : 0,
    dominantAngle,
    directionCoherence,
    occlusionBoundaries: vectors.filter((vector) => vector.occlusionBoundary).length,
    occlusionWeighted: vectors.filter((vector) => vector.occlusionWeight < 0.6).length,
    lowTexture: vectors.filter((vector) => vector.textureScore < 0.18).length,
    meanConfidence: vectors.length
      ? vectors.reduce((sum, vector) => sum + vector.confidence, 0) / vectors.length
      : 0,
  };
}

export function angleForVector(vector) {
  return (Math.atan2(vector.vy, vector.vx) * 180 / Math.PI + 360) % 360;
}

export function directionDistance(a, b) {
  const difference = Math.abs(a - b) % 360;
  return Math.min(difference, 360 - difference);
}

export const UNIT_DEFINITIONS = {
  coordinate_system: "Image pixel coordinates, origin at top-left, +x right, +y down.",
  displacement_units: "Pixels per frame pair (frame A to frame B).",
  position_units: "Pixels at block center.",
  angle_units: "Degrees clockwise from +x in screen coordinates (0 right, 90 down).",
  confidence: "Dimensionless 0..1 reliability score combining texture, match cost, ambiguity, and occlusion consistency.",
  occlusion_weight: "Dimensionless 0..1 forward-backward and motion-discontinuity reliability weight.",
  cost: "Mean absolute luma difference (0..255 gray levels) over the block.",
  epe: "Endpoint error in pixels between estimated and known displacement.",
};

