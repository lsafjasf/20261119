import { directionDistance } from "./validation.js";

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

const CONFIDENCE_STOPS = [
  [0, [16, 26, 59]],
  [0.28, [31, 63, 132]],
  [0.52, [28, 176, 157]],
  [0.76, [152, 226, 111]],
  [1, [241, 255, 191]],
];

const COST_STOPS = [
  [0, [17, 29, 61]],
  [0.35, [74, 55, 128]],
  [0.68, [164, 67, 132]],
  [1, [255, 182, 95]],
];

function rampColor(stops, value) {
  const v = clamp(value, 0, 1);
  for (let i = 1; i < stops.length; i += 1) {
    if (v <= stops[i][0]) {
      const a = stops[i - 1];
      const b = stops[i];
      const t = (v - a[0]) / Math.max(0.0001, b[0] - a[0]);
      return [
        Math.round(a[1][0] + (b[1][0] - a[1][0]) * t),
        Math.round(a[1][1] + (b[1][1] - a[1][1]) * t),
        Math.round(a[1][2] + (b[1][2] - a[1][2]) * t),
      ];
    }
  }
  return stops[stops.length - 1][1];
}

function confidenceColor(value) {
  const [r, g, b] = rampColor(CONFIDENCE_STOPS, value);
  return `rgb(${r} ${g} ${b})`;
}

function costColor(value) {
  const [r, g, b] = rampColor(COST_STOPS, value);
  return `rgb(${r} ${g} ${b})`;
}

function drawArrow(ctx, x, y, vx, vy, gain, color, alpha, width = 1.15) {
  const magnitude = Math.hypot(vx, vy);
  if (magnitude < 0.1) {
    ctx.globalAlpha = alpha * 0.55;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x, y, 1.25, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1;
    return;
  }
  const length = clamp(magnitude * gain, 2.5, 58);
  const ux = vx / magnitude;
  const uy = vy / magnitude;
  const startX = x - ux * 1.5;
  const startY = y - uy * 1.5;
  const endX = x + ux * length;
  const endY = y + uy * length;
  const head = clamp(2.4 + length * 0.09, 2.8, 5.2);
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = width;
  ctx.beginPath();
  ctx.moveTo(startX, startY);
  ctx.lineTo(endX, endY);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(endX, endY);
  ctx.lineTo(endX - ux * head - uy * head * 0.52, endY - uy * head + ux * head * 0.52);
  ctx.lineTo(endX - ux * head + uy * head * 0.52, endY - uy * head - ux * head * 0.52);
  ctx.closePath();
  ctx.fill();
  ctx.globalAlpha = 1;
}

export function renderFrames(canvasA, canvasB, scene) {
  for (const [canvas, frame] of [[canvasA, scene.frameA], [canvasB, scene.frameB]]) {
    if (canvas.width !== frame.width) {
      canvas.width = frame.width * 2;
      canvas.height = frame.height * 2;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(frame, 0, 0, canvas.width, canvas.height);
  }
}

export function renderField(canvas, scene, field, options, selectedIndex = null) {
  const width = field.width;
  const height = field.height;
  const scale = 2;
  const expectedWidth = width * scale;
  if (canvas.width !== expectedWidth) {
    canvas.width = expectedWidth;
    canvas.height = height * scale;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.drawImage(scene.frameA, 0, 0, width, height);
  ctx.fillStyle = "rgba(4, 9, 13, 0.44)";
  ctx.fillRect(0, 0, width, height);

  if (options.layer === "confidence") {
    for (const vector of field.vectors) {
      if (vector.confidence < 0.035) continue;
      ctx.globalAlpha = 0.22 + vector.confidence * 0.44;
      ctx.fillStyle = confidenceColor(vector.confidence);
      ctx.fillRect(
        vector.x - field.stride / 2,
        vector.y - field.stride / 2,
        field.stride,
        field.stride,
      );
    }
    ctx.globalAlpha = 1;
  } else if (options.layer === "cost") {
    for (const vector of field.vectors) {
      const normalized = Number.isFinite(vector.cost) ? clamp(vector.cost / 50, 0, 1) : 1;
      ctx.globalAlpha = 0.68;
      ctx.fillStyle = costColor(normalized);
      ctx.fillRect(
        vector.x - field.stride / 2,
        vector.y - field.stride / 2,
        field.stride,
        field.stride,
      );
    }
    ctx.globalAlpha = 1;
  }

  if (options.showGroundTruth && field.vectors[0]?.gtVx != null) {
    for (const vector of field.vectors) {
      if (!vector.gtValid) continue;
      const gx = vector.gtVx;
      const gy = vector.gtVy;
      const magnitude = Math.hypot(gx, gy);
      if (magnitude < 0.05) continue;
      const ux = gx / magnitude;
      const uy = gy / magnitude;
      const length = clamp(magnitude * options.gain, 2, 58);
      ctx.globalAlpha = 0.28;
      ctx.strokeStyle = "#ff79c8";
      ctx.lineWidth = 0.8;
      ctx.setLineDash([2, 3]);
      ctx.beginPath();
      ctx.moveTo(vector.x, vector.y);
      ctx.lineTo(vector.x + ux * length, vector.y + uy * length);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#ff79c8";
      ctx.beginPath();
      ctx.arc(vector.x + ux * length, vector.y + uy * length, 1.15, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  if (options.showVectors) {
    for (const vector of field.vectors) {
      const angle = (Math.atan2(vector.vy, vector.vx) * 180 / Math.PI + 360) % 360;
      if (options.directionEnabled && directionDistance(angle, options.directionCenter) > 25) {
        continue;
      }
      if (vector.confidence < options.threshold) {
        ctx.globalAlpha = 0.2;
        ctx.strokeStyle = "#ffb65f";
        ctx.lineWidth = 0.8;
        ctx.beginPath();
        ctx.arc(vector.x, vector.y, 1.4, 0, Math.PI * 2);
        ctx.stroke();
        continue;
      }
      const color = confidenceColor(vector.confidence);
      const alpha = 0.55 + vector.confidence * 0.4;
      drawArrow(
        ctx,
        vector.x,
        vector.y,
        vector.vx,
        vector.vy,
        options.gain,
        color,
        alpha,
        0.9 + vector.confidence * 0.55,
      );
    }
    ctx.globalAlpha = 1;
  }

  if (options.showOcclusion) {
    for (const vector of field.vectors) {
      if (!vector.occlusionBoundary) continue;
      const half = field.blockSize / 2;
      ctx.globalAlpha = 0.55 + (1 - vector.occlusionWeight) * 0.35;
      ctx.strokeStyle = "#ff79c8";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(vector.x - half + 2, vector.y - half + 5);
      ctx.lineTo(vector.x - half + 2, vector.y - half + 2);
      ctx.lineTo(vector.x - half + 5, vector.y - half + 2);
      ctx.moveTo(vector.x + half - 5, vector.y + half - 2);
      ctx.lineTo(vector.x + half - 2, vector.y + half - 2);
      ctx.lineTo(vector.x + half - 2, vector.y + half - 5);
      ctx.stroke();
      ctx.fillStyle = "#ff79c8";
      ctx.beginPath();
      ctx.arc(vector.x, vector.y, 1.5, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  if (selectedIndex != null && field.vectors[selectedIndex]) {
    const vector = field.vectors[selectedIndex];
    const half = field.blockSize / 2 + 3;
    ctx.globalAlpha = 1;
    ctx.strokeStyle = "#f2ffff";
    ctx.lineWidth = 1.3;
    ctx.setLineDash([3, 2]);
    ctx.strokeRect(vector.x - half, vector.y - half, half * 2, half * 2);
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(4, 9, 13, 0.86)";
    ctx.fillRect(vector.x - 20, vector.y - half - 13, 40, 9);
    ctx.fillStyle = "#f2ffff";
    ctx.font = "5px 'DM Mono', monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(`BLOCK ${vector.index}`, vector.x, vector.y - half - 8.5);
  }
}

export function renderEpeChart(canvas, histogram) {
  const logicalWidth = 260;
  const logicalHeight = 74;
  if (canvas.width !== logicalWidth * 2) {
    canvas.width = logicalWidth * 2;
    canvas.height = logicalHeight * 2;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(2, 0, 0, 2, 0, 0);
  ctx.clearRect(0, 0, logicalWidth, logicalHeight);
  const max = Math.max(1, ...histogram);
  const chartTop = 8;
  const chartBottom = 58;
  const barGap = 4;
  const barWidth = (logicalWidth - barGap * (histogram.length - 1)) / histogram.length;
  ctx.strokeStyle = "rgba(151, 185, 199, .1)";
  ctx.lineWidth = 1;
  for (let i = 0; i < 3; i += 1) {
    const y = chartTop + ((chartBottom - chartTop) / 2) * i + 0.5;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(logicalWidth, y);
    ctx.stroke();
  }
  histogram.forEach((count, index) => {
    const height = Math.max(count ? 2 : 0, (count / max) * (chartBottom - chartTop));
    const x = index * (barWidth + barGap);
    const gradient = ctx.createLinearGradient(0, chartBottom - height, 0, chartBottom);
    gradient.addColorStop(0, index > 3 ? "#ffb65f" : "#4de8d2");
    gradient.addColorStop(1, index > 3 ? "rgba(255,182,95,.2)" : "rgba(77,232,210,.14)");
    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.roundRect(x, chartBottom - height, barWidth, height, 2);
    ctx.fill();
  });
  ctx.fillStyle = "#657a84";
  ctx.font = "6.5px 'DM Mono', monospace";
  ctx.textAlign = "left";
  ctx.fillText("0", 0, 69);
  ctx.textAlign = "center";
  ctx.fillText("1", logicalWidth * 0.25, 69);
  ctx.fillText("2", logicalWidth * 0.5, 69);
  ctx.fillText("3", logicalWidth * 0.75, 69);
  ctx.textAlign = "right";
  ctx.fillText("4+", logicalWidth, 69);
}

