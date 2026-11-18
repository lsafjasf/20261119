const COLORS = {
  acid: "#c9ff4b",
  cyan: "#62e7ef",
  orange: "#ff9a55",
  white: "#eef5ec",
  muted: "#657067",
  faint: "#3f4942",
  grid: "rgba(255,255,255,0.055)",
};

function prepare(canvas) {
  const rect = canvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(1, Math.round(rect.width));
  const height = Math.max(1, Math.round(rect.height));
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
  }
  const context = canvas.getContext("2d");
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, width, height);
  return { context, width, height };
}

function orderedRing(values, cursor) {
  if (!cursor || cursor >= values.length) return values;
  const result = new Float32Array(values.length);
  result.set(values.subarray(cursor));
  result.set(values.subarray(0, cursor), values.length - cursor);
  return result;
}

function pathWave(context, values, width, height, color, alpha = 1, gain = 0.76) {
  if (!values || values.length < 2) return;
  context.save();
  context.strokeStyle = color;
  context.globalAlpha = alpha;
  context.lineWidth = 1.35;
  context.lineJoin = "round";
  context.beginPath();
  const center = height * 0.5;
  const scale = height * 0.5 * gain;
  for (let index = 0; index < values.length; index += 1) {
    const x = (index / (values.length - 1)) * width;
    const y = center - Math.max(-2, Math.min(2, values[index])) * scale;
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  }
  context.stroke();
  context.restore();
}

export function drawScope(canvas, telemetry) {
  const { context, width, height } = prepare(canvas);
  if (!telemetry || !telemetry.scopeIn) {
    context.fillStyle = COLORS.faint;
    context.font = "9px ui-monospace, monospace";
    context.fillText("WAITING FOR AUDIO THREAD", 28, 28);
    return;
  }
  const input = orderedRing(telemetry.scopeIn, telemetry.scopeCursor);
  const output = orderedRing(telemetry.scopeOut, telemetry.scopeCursor);
  pathWave(context, input, width, height, COLORS.cyan, 0.72);
  pathWave(context, output, width, height, COLORS.acid, 0.94);
}

function drawRingPhase(context, x, y, radius, telemetry) {
  context.save();
  context.strokeStyle = "rgba(255,255,255,0.12)";
  context.lineWidth = 1;
  context.beginPath();
  context.arc(x, y, radius, 0, Math.PI * 2);
  context.stroke();

  for (let tick = 0; tick < 36; tick += 1) {
    const angle = (tick / 36) * Math.PI * 2 - Math.PI / 2;
    const inner = radius - (tick % 3 === 0 ? 6 : 3);
    context.globalAlpha = tick % 3 === 0 ? 0.28 : 0.12;
    context.beginPath();
    context.moveTo(x + Math.cos(angle) * inner, y + Math.sin(angle) * inner);
    context.lineTo(x + Math.cos(angle) * radius, y + Math.sin(angle) * radius);
    context.stroke();
  }

  context.globalAlpha = 1;
  context.fillStyle = COLORS.faint;
  context.font = "7px ui-monospace, monospace";
  context.textAlign = "center";
  context.fillText("RING", x, y + 3);
  context.textAlign = "left";

  if (!telemetry) {
    context.restore();
    return;
  }
  const size = telemetry.ringSize || 1;
  const marker = (position, color, label, offset) => {
    const angle = (position / size) * Math.PI * 2 - Math.PI / 2;
    const px = x + Math.cos(angle) * (radius + 5);
    const py = y + Math.sin(angle) * (radius + 5);
    context.fillStyle = color;
    context.beginPath();
    context.arc(px, py, 2.3, 0, Math.PI * 2);
    context.fill();
    if (label) {
      context.font = "7px ui-monospace, monospace";
      context.fillText(label, px + 5, py + offset);
    }
  };
  marker(telemetry.writePosition, COLORS.white, "W", 2);
  marker(telemetry.readPositionA, COLORS.acid, "A", 2);
  marker(telemetry.readPositionB, COLORS.orange, "B", 8);
  context.restore();
}

function drawTrack(context, width, height, telemetry) {
  const left = 22;
  const right = width - 22;
  const writeX = right;
  const span = right - left;
  const trackY = height - 54;
  const delayA = telemetry ? (telemetry.delayA / telemetry.sampleRate) * 1000 : 23.4;
  const delayB = telemetry ? (telemetry.delayB / telemetry.sampleRate) * 1000 : 23.4;
  const target = telemetry ? (telemetry.targetDelay / telemetry.sampleRate) * 1000 : 23.4;
  const viewMs = Math.min(520, Math.max(18, Math.max(delayA, delayB, target) * 1.32));
  const xForDelay = (delay) => writeX - (delay / viewMs) * span;

  const gradient = context.createLinearGradient(left, 0, right, 0);
  gradient.addColorStop(0, "rgba(201,255,75,0.015)");
  gradient.addColorStop(0.78, "rgba(201,255,75,0.055)");
  gradient.addColorStop(1, "rgba(201,255,75,0.13)");
  context.fillStyle = gradient;
  context.fillRect(left, trackY - 14, span, 28);

  for (let tick = 0; tick < 18; tick += 1) {
    const x = left + (tick / 17) * span;
    context.strokeStyle = "rgba(255,255,255,0.045)";
    context.beginPath();
    context.moveTo(x, trackY - 14);
    context.lineTo(x, trackY + 14);
    context.stroke();
  }

  context.strokeStyle = "rgba(255,255,255,0.16)";
  context.beginPath();
  context.moveTo(left, trackY);
  context.lineTo(right, trackY);
  context.stroke();

  if (telemetry) {
    const effectiveDelay = (telemetry.effectiveDelay / telemetry.sampleRate) * 1000;
    const effectiveX = xForDelay(effectiveDelay);
    context.strokeStyle = "rgba(238,245,236,0.22)";
    context.setLineDash([3, 4]);
    context.beginPath();
    context.moveTo(effectiveX, trackY - 29);
    context.lineTo(effectiveX, trackY + 25);
    context.stroke();
    context.setLineDash([]);
    context.fillStyle = COLORS.white;
    context.beginPath();
    context.moveTo(effectiveX, trackY - 19);
    context.lineTo(effectiveX + 4, trackY - 14);
    context.lineTo(effectiveX, trackY - 9);
    context.lineTo(effectiveX - 4, trackY - 14);
    context.closePath();
    context.fill();
  }

  const drawHead = (delay, color, label, gain, offset) => {
    const x = xForDelay(delay);
    const alpha = 0.28 + gain * 0.72;
    context.strokeStyle = color;
    context.globalAlpha = alpha;
    context.lineWidth = 1.4;
    context.beginPath();
    context.moveTo(x, trackY - 27);
    context.lineTo(x, trackY + 22);
    context.stroke();
    context.fillStyle = color;
    context.beginPath();
    context.arc(x, trackY, 3.8, 0, Math.PI * 2);
    context.fill();
    context.globalAlpha = 1;
    context.font = "8px ui-monospace, monospace";
    context.fillText(label, x - 3, trackY - 35 + offset);
    context.fillText(`${delay.toFixed(2)}`, x - 13, trackY + 37 + offset * 0.3);
  };

  if (telemetry) {
    drawHead(delayA, COLORS.acid, "A", telemetry.gainA ?? 1, 0);
    drawHead(delayB, COLORS.orange, "B", telemetry.gainB ?? 0, 0);
  } else {
    drawHead(delayA, COLORS.acid, "A", 1, 0);
    drawHead(delayB, COLORS.orange, "B", 0, 0);
  }

  context.strokeStyle = COLORS.white;
  context.lineWidth = 1.6;
  context.beginPath();
  context.moveTo(writeX, trackY - 25);
  context.lineTo(writeX, trackY + 20);
  context.stroke();
  context.fillStyle = COLORS.white;
  context.font = "8px ui-monospace, monospace";
  context.fillText("W", writeX - 4, trackY - 33);
  context.fillText("NOW", writeX - 18, trackY + 35);

  context.fillStyle = COLORS.faint;
  context.font = "7px ui-monospace, monospace";
  context.fillText(`AUTO ZOOM / ${viewMs.toFixed(1)} ms`, left, trackY + 39);
}

export function drawMap(canvas, telemetry) {
  const { context, width, height } = prepare(canvas);
  context.fillStyle = "rgba(255,255,255,0.025)";
  for (let x = 0; x < width; x += 40) context.fillRect(x, 0, 1, height);
  drawTrack(context, width, height, telemetry);
  if (width > 390) {
    drawRingPhase(context, width - 73, 78, 45, telemetry);
  }
}

function drawChartBase(context, width, height) {
  context.fillStyle = "rgba(0,0,0,0.06)";
  context.fillRect(0, 0, width, height);
}

function drawPolyline(context, xs, ys, color, lineWidth = 1.4) {
  if (!xs.length) return;
  context.strokeStyle = color;
  context.lineWidth = lineWidth;
  context.lineJoin = "round";
  context.beginPath();
  for (let index = 0; index < xs.length; index += 1) {
    if (index === 0) context.moveTo(xs[index], ys[index]);
    else context.lineTo(xs[index], ys[index]);
  }
  context.stroke();
}

export function drawEmptyChart(canvas, label = "QUEUED") {
  const { context, width, height } = prepare(canvas);
  drawChartBase(context, width, height);
  context.fillStyle = COLORS.faint;
  context.font = "8px ui-monospace, monospace";
  context.fillText(label, 10, 18);
}

export function drawImpulseChart(canvas, data) {
  const { context, width, height } = prepare(canvas);
  drawChartBase(context, width, height);
  if (!data) return drawEmptyChart(canvas);
  const pad = 8;
  const centerY = height * 0.53;
  const linear = data.linear.trace;
  const cubic = data.cubic.trace;
  const allValues = [...linear.values, ...cubic.values];
  const peak = Math.max(0.0001, ...allValues.map(Math.abs));
  const minOffset = Math.min(...linear.offsets);
  const maxOffset = Math.max(...linear.offsets);
  const xFor = (offset) => pad + ((offset - minOffset) / (maxOffset - minOffset)) * (width - pad * 2);
  const yFor = (value) => centerY - (value / (peak * 1.12)) * (height * 0.38);

  context.strokeStyle = COLORS.grid;
  context.beginPath();
  context.moveTo(pad, centerY + 0.5);
  context.lineTo(width - pad, centerY + 0.5);
  context.stroke();
  context.strokeStyle = "rgba(255,255,255,0.14)";
  context.setLineDash([2, 3]);
  context.beginPath();
  context.moveTo(xFor(0), pad);
  context.lineTo(xFor(0), height - pad);
  context.stroke();
  context.setLineDash([]);

  const linearX = linear.offsets.map(xFor);
  const cubicX = cubic.offsets.map(xFor);
  drawPolyline(context, linearX, linear.values.map(yFor), COLORS.cyan, 1.5);
  drawPolyline(context, cubicX, cubic.values.map(yFor), COLORS.orange, 1.5);
  context.fillStyle = COLORS.faint;
  context.font = "7px ui-monospace, monospace";
  context.fillText("TARGET", xFor(0) + 4, 14);
}

export function drawSweepChart(canvas, data) {
  const { context, width, height } = prepare(canvas);
  drawChartBase(context, width, height);
  if (!data) return drawEmptyChart(canvas);
  const pad = 9;
  const pointsLinear = data.linear.trace.points;
  const pointsCubic = data.cubic.trace.points;
  const allDb = [...pointsLinear, ...pointsCubic].map((point) => point.db);
  const yMax = Math.max(1, Math.ceil(Math.max(...allDb) + 0.5));
  const yMin = Math.min(-3, Math.floor(Math.min(...allDb) - 0.5));
  const xFor = (frequency) =>
    pad + (Math.log(frequency / 35) / Math.log(18000 / 35)) * (width - pad * 2);
  const yFor = (db) => pad + ((yMax - db) / (yMax - yMin)) * (height - pad * 2);

  for (let db = Math.ceil(yMin); db <= yMax; db += 1) {
    if (db % 2 !== 0 && db !== 0) continue;
    context.strokeStyle = db === 0 ? "rgba(255,255,255,0.16)" : COLORS.grid;
    context.beginPath();
    context.moveTo(pad, yFor(db));
    context.lineTo(width - pad, yFor(db));
    context.stroke();
  }

  for (const frequency of [100, 1000, 10000]) {
    const x = xFor(frequency);
    context.strokeStyle = COLORS.grid;
    context.beginPath();
    context.moveTo(x, pad);
    context.lineTo(x, height - pad);
    context.stroke();
    context.fillStyle = COLORS.faint;
    context.font = "6px ui-monospace, monospace";
    context.fillText(frequency >= 1000 ? `${frequency / 1000}k` : `${frequency}`, x + 3, height - 3);
  }

  drawPolyline(
    context,
    pointsLinear.map((point) => xFor(point.frequency)),
    pointsLinear.map((point) => yFor(point.db)),
    COLORS.cyan,
  );
  drawPolyline(
    context,
    pointsCubic.map((point) => xFor(point.frequency)),
    pointsCubic.map((point) => yFor(point.db)),
    COLORS.orange,
  );
}

export function drawJumpChart(canvas, data) {
  const { context, width, height } = prepare(canvas);
  drawChartBase(context, width, height);
  if (!data) return drawEmptyChart(canvas);
  const pad = 8;
  const values = [...data.linear.trace.values, ...data.cubic.trace.values];
  const yMax = Math.max(1.35, Math.min(4, Math.max(...values) * 1.08));
  const yFor = (value) => height - pad - (value / yMax) * (height - pad * 2);
  const xFor = (time, duration) => pad + (time / duration) * (width - pad * 2);
  const events = data.linear.trace.events;
  const duration = data.linear.trace.duration;

  context.strokeStyle = "rgba(255,255,255,0.16)";
  context.beginPath();
  context.moveTo(pad, yFor(1));
  context.lineTo(width - pad, yFor(1));
  context.stroke();
  context.fillStyle = COLORS.faint;
  context.font = "6px ui-monospace, monospace";
  context.fillText("1.0×", 3, yFor(1) - 3);

  for (const event of events) {
    const x = xFor(event, duration);
    context.strokeStyle = "rgba(255,154,85,0.13)";
    context.beginPath();
    context.moveTo(x, pad);
    context.lineTo(x, height - pad);
    context.stroke();
  }

  const drawEnvelope = (trace, color) => {
    const xs = trace.values.map((_, index) =>
      xFor((index / (trace.values.length - 1)) * trace.duration, trace.duration),
    );
    drawPolyline(context, xs, trace.values.map(yFor), color, 1.25);
  };
  drawEnvelope(data.linear.trace, COLORS.cyan);
  drawEnvelope(data.cubic.trace, COLORS.orange);
}
