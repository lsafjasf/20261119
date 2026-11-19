import {
  analyzeLuminance,
  buildAutoMask,
  combineMasks,
  detectPeakCandidates,
  findPeakNear,
  maskCoverage,
  measureQuality,
  paintMask,
  reconstructRgba,
  rgbaToLuminance,
} from "./processing.js";
import { generateTestSample, sampleMetadata } from "./texture.js";
import { createZip } from "./zip.js";

const SIZE = 512;
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const dom = {
  compareCanvas: $("#compareCanvas"),
  compareStage: $("#compareStage"),
  splitHandle: $("#splitHandle"),
  tagBefore: $(".tag-before"),
  tagAfter: $(".tag-after"),
  spectrumCanvas: $("#spectrumCanvas"),
  peakList: $("#peakList"),
  energyMetric: $("#energyMetric"),
  edgeMetric: $("#edgeMetric"),
  ringingMetric: $("#ringingMetric"),
  energyBar: $("#energyBar"),
  edgeMarker: $("#edgeMarker"),
  ringingMarker: $("#ringingMarker"),
  maskReadout: $("#maskReadout"),
  selectedPeakCount: $("#selectedPeakCount"),
  totalPeakCount: $("#totalPeakCount"),
  radiusRange: $("#radiusRange"),
  softnessRange: $("#softnessRange"),
  strengthRange: $("#strengthRange"),
  radiusValue: $("#radiusValue"),
  softnessValue: $("#softnessValue"),
  strengthValue: $("#strengthValue"),
  brushRange: $("#brushRange"),
  brushValue: $("#brushValue"),
  frameReadout: $("#frameReadout"),
  spectrumHint: $("#spectrumHint"),
  toast: $("#toast"),
  loadingVeil: $("#loadingVeil"),
  batchStatus: $("#batchStatus"),
  maskFileInput: $("#maskFileInput"),
  batchFileInput: $("#batchFileInput"),
};

const state = {
  seed: 1847,
  sample: null,
  analysis: null,
  baseLuminance: null,
  sourceLuminance: null,
  peaks: [],
  autoMask: null,
  manualMask: null,
  combinedMask: null,
  maskOverride: null,
  reconstructed: null,
  reconstructedLuminance: null,
  metrics: null,
  compareMode: "split",
  split: 0.5,
  painting: false,
  paintRestore: false,
  lastPaint: null,
  spectrumBoost: false,
  processTimer: 0,
  spectrumTimer: 0,
  busy: false,
  batchMask: null,
  batchMaskSize: SIZE,
  spectrumGeometry: null,
  zoomLabel: "1:1 DISPLAY",
};

const sourceCanvas = document.createElement("canvas");
const reconstructionCanvas = document.createElement("canvas");
const spectrumBuffer = document.createElement("canvas");
const maskBuffer = document.createElement("canvas");
for (const canvas of [sourceCanvas, reconstructionCanvas, spectrumBuffer, maskBuffer]) {
  canvas.width = SIZE;
  canvas.height = SIZE;
}

let toastTimer = 0;
let resizeTimer = 0;

function showToast(message, type = "info") {
  window.clearTimeout(toastTimer);
  dom.toast.textContent = message;
  dom.toast.classList.toggle("error", type === "error");
  dom.toast.classList.add("show");
  toastTimer = window.setTimeout(() => dom.toast.classList.remove("show"), 3400);
}

function setLoading(loading) {
  dom.loadingVeil.classList.toggle("show", loading);
}

function nextPaint() {
  return new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve, 16)));
}

function formatNumber(value, digits = 1) {
  return new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

function putRgba(canvas, rgba) {
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.putImageData(new ImageData(rgba, SIZE, SIZE), 0, 0);
}

function createPeakList(sample, analysis) {
  const metadata = sampleMetadata();
  const peaks = [];
  const known = [];

  for (const [index, component] of metadata.interference.entries()) {
    const measured = findPeakNear(analysis.magnitude, SIZE, component.fx, component.fy, 5);
    if (!measured) continue;
    known.push(measured);
    peaks.push({
      id: `interference-${index}`,
      code: `P${index + 1}`,
      label: component.label,
      type: "interference",
      fx: measured.fx,
      fy: measured.fy,
      magnitude: measured.magnitude,
      enabled: true,
    });
  }

  for (const [index, component] of metadata.texture.entries()) {
    const measured = findPeakNear(analysis.magnitude, SIZE, component.fx, component.fy, 5);
    if (!measured) continue;
    known.push(measured);
    peaks.push({
      id: `texture-${index}`,
      code: `T${index + 1}`,
      label: component.label,
      type: "texture",
      fx: measured.fx,
      fy: measured.fy,
      magnitude: measured.magnitude,
      enabled: false,
    });
  }

  const candidates = detectPeakCandidates(analysis.magnitude, SIZE, 24);
  let extraIndex = 0;
  for (const candidate of candidates) {
    if (extraIndex >= 1) break;
    const nearKnown = known.some((item) =>
      Math.hypot(item.x - candidate.x, item.y - candidate.y) < 10 ||
      Math.hypot(item.x - (SIZE - candidate.x), item.y - (SIZE - candidate.y)) < 10,
    );
    if (nearKnown) continue;
    known.push(candidate);
    peaks.push({
      id: `detected-${extraIndex}`,
      code: `N${extraIndex + 1}`,
      label: "未分类窄带峰",
      type: "detected",
      fx: candidate.fx,
      fy: candidate.fy,
      magnitude: candidate.magnitude,
      enabled: false,
    });
    extraIndex += 1;
  }

  return peaks;
}

function newManualMask() {
  const mask = new Float32Array(SIZE * SIZE);
  mask.fill(1);
  return mask;
}

async function loadSample(seed = state.seed) {
  if (state.busy) return;
  state.busy = true;
  setLoading(true);
  await nextPaint();
  try {
    state.seed = seed;
    state.sample = generateTestSample(seed, SIZE);
    state.baseLuminance = rgbaToLuminance(state.sample.base, SIZE);
    state.sourceLuminance = rgbaToLuminance(state.sample.source, SIZE);
    state.analysis = analyzeLuminance(state.sample.source, SIZE);
    state.peaks = createPeakList(state.sample, state.analysis);
    state.manualMask = newManualMask();
    state.maskOverride = null;
    state.batchMask = null;
    state.batchMaskSize = SIZE;
    putRgba(sourceCanvas, state.sample.source);
    renderPeakList();
    processFrame();
    dom.frameReadout.textContent = `TEXTILE · ${SIZE}²`;
    dom.batchStatus.className = "batch-status";
    dom.batchStatus.innerHTML = '<span class="status-dot"></span><span>等待载入同尺寸批次</span>';
    requestAnimationFrame(renderSpectrum);
  } finally {
    state.busy = false;
    setLoading(false);
  }
}

function getSettings() {
  return {
    radius: Number(dom.radiusRange.value),
    softness: Number(dom.softnessRange.value) / 100,
    strength: Number(dom.strengthRange.value) / 100,
  };
}

function scheduleProcess(delay = 70) {
  window.clearTimeout(state.processTimer);
  state.processTimer = window.setTimeout(processFrame, delay);
}

function invalidateLoadedMask() {
  if (!state.batchMask) return;
  state.batchMask = null;
  state.batchMaskSize = SIZE;
  setBatchStatus(`当前掩码已更新 · ${SIZE}² · 可执行批处理`, "done");
}

function processFrame() {
  if (!state.sample || !state.analysis) return;
  const settings = getSettings();
  state.autoMask = buildAutoMask(SIZE, state.peaks, settings);
  state.combinedMask = state.maskOverride || combineMasks(state.autoMask, state.manualMask, SIZE);
  const result = reconstructRgba(state.sample.source, SIZE, state.analysis, state.combinedMask);
  state.reconstructed = result.rgba;
  state.reconstructedLuminance = result.filteredLuminance;
  state.metrics = measureQuality({
    size: SIZE,
    baseLuminance: state.baseLuminance,
    sourceLuminance: state.sourceLuminance,
    reconstructedLuminance: result.filteredLuminance,
    spectrum: state.analysis.spectrum,
    mask: state.combinedMask,
  });
  state.metrics.coverage = maskCoverage(state.combinedMask);
  putRgba(reconstructionCanvas, result.rgba);
  updateMetrics();
  updatePeakCount();
  renderCompare();
  scheduleSpectrum();
}

function updateMetrics() {
  if (!state.metrics) return;
  const { energyReduction, edgeChange, ringing, coverage } = state.metrics;
  dom.energyMetric.innerHTML = `${formatNumber(energyReduction, 1)}<small>%</small>`;
  const edgeSign = edgeChange >= 0 ? "+" : "";
  dom.edgeMetric.innerHTML = `${edgeSign}${formatNumber(edgeChange, 2)}<small>%</small>`;
  dom.ringingMetric.innerHTML = `${formatNumber(ringing, 2)}<small>%</small>`;
  dom.energyBar.style.width = `${Math.max(2, Math.min(100, energyReduction))}%`;
  const edgePosition = Math.max(5, Math.min(95, 50 + edgeChange * 3.4));
  dom.edgeMarker.style.left = `${edgePosition}%`;
  const ringingPosition = Math.max(5, Math.min(95, 8 + ringing * 14));
  dom.ringingMarker.style.left = `${ringingPosition}%`;
  const selected = state.peaks.filter((peak) => peak.enabled).length;
  dom.maskReadout.textContent = `${selected} PAIRS · ${formatNumber(coverage, 2)}% AREA`;
}

function updatePeakCount() {
  const selected = state.peaks.filter((peak) => peak.enabled).length;
  dom.selectedPeakCount.textContent = String(selected);
  dom.totalPeakCount.textContent = String(state.peaks.length);
}

function resizeCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(1, Math.round(rect.width));
  const height = Math.max(1, Math.round(rect.height));
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
  }
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { context, width, height };
}

function drawCover(context, image, width, height, alpha = 1) {
  const scale = Math.max(width / SIZE, height / SIZE);
  const drawWidth = SIZE * scale;
  const drawHeight = SIZE * scale;
  context.globalAlpha = alpha;
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = "high";
  context.drawImage(image, (width - drawWidth) / 2, (height - drawHeight) / 2, drawWidth, drawHeight);
  context.globalAlpha = 1;
}

function renderCompare() {
  if (!state.sample || !state.reconstructed) return;
  const { context, width, height } = resizeCanvas(dom.compareCanvas);
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#030706";
  context.fillRect(0, 0, width, height);

  const splitX = width * state.split;
  if (state.compareMode === "before") {
    drawCover(context, sourceCanvas, width, height);
  } else if (state.compareMode === "after") {
    drawCover(context, reconstructionCanvas, width, height);
  } else {
    drawCover(context, sourceCanvas, width, height);
    context.save();
    context.beginPath();
    context.rect(splitX, 0, width - splitX, height);
    context.clip();
    drawCover(context, reconstructionCanvas, width, height);
    context.restore();
    context.save();
    context.strokeStyle = "rgba(3, 8, 7, 0.26)";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(splitX, 0);
    context.lineTo(splitX, height);
    context.stroke();
    context.restore();
  }
  dom.splitHandle.style.display = state.compareMode === "split" ? "block" : "none";
  dom.splitHandle.style.left = `${splitX}px`;
  dom.tagBefore.style.display = state.compareMode === "after" ? "none" : "inline-flex";
  dom.tagAfter.style.display = state.compareMode === "before" ? "none" : "inline-flex";
}

function typeText(type) {
  if (type === "interference") return "干扰";
  if (type === "texture") return "真实纹理";
  return "窄带";
}

function renderPeakList() {
  dom.peakList.innerHTML = state.peaks.map((peak) => `
    <label class="peak-row ${peak.type} ${peak.enabled ? "selected" : "disabled"}" data-peak-id="${peak.id}">
      <input class="peak-check" type="checkbox" ${peak.enabled ? "checked" : ""} aria-label="启用 ${peak.label}">
      <span class="peak-main">
        <span class="peak-title">
          <strong>${peak.code} · ${peak.label}</strong>
          <span class="peak-type ${peak.type}">${typeText(peak.type)}</span>
        </span>
        <span class="peak-meta">
          <span>+f (${peak.fx.toFixed(4)}, ${peak.fy.toFixed(4)})</span>
          <span>−f 已配对</span>
        </span>
      </span>
      <span class="peak-strength">${peak.magnitude.toExponential(1)}</span>
      <span class="peak-pair-glyph" aria-hidden="true"><i></i></span>
    </label>
  `).join("");
  updatePeakCount();
  requestAnimationFrame(renderSpectrum);
}

dom.peakList.addEventListener("change", (event) => {
  if (!event.target.matches(".peak-check")) return;
  const row = event.target.closest(".peak-row");
  const peak = state.peaks.find((item) => item.id === row.dataset.peakId);
  if (!peak) return;
  invalidateLoadedMask();
  state.maskOverride = null;
  peak.enabled = event.target.checked;
  row.classList.toggle("selected", peak.enabled);
  row.classList.toggle("disabled", !peak.enabled);
  renderPeakList();
  scheduleProcess(20);
});

const COLOR_STOPS = [
  [0, [3, 10, 10]],
  [0.16, [6, 33, 33]],
  [0.34, [12, 73, 68]],
  [0.52, [34, 126, 109]],
  [0.7, [105, 198, 160]],
  [0.85, [233, 209, 132]],
  [1, [255, 248, 219]],
];

function colorFor(value) {
  for (let i = 1; i < COLOR_STOPS.length; i += 1) {
    const [position, color] = COLOR_STOPS[i];
    const [previousPosition, previousColor] = COLOR_STOPS[i - 1];
    if (value <= position) {
      const t = (value - previousPosition) / (position - previousPosition);
      return [
        previousColor[0] + (color[0] - previousColor[0]) * t,
        previousColor[1] + (color[1] - previousColor[1]) * t,
        previousColor[2] + (color[2] - previousColor[2]) * t,
      ];
    }
  }
  return COLOR_STOPS[COLOR_STOPS.length - 1][1];
}

function paintSpectrumBuffer() {
  if (!state.analysis || !state.combinedMask) return;
  const context = spectrumBuffer.getContext("2d", { willReadFrequently: true });
  const image = context.createImageData(SIZE, SIZE);
  const { magnitude, max } = state.analysis;
  const denominator = Math.log1p(90);
  const boost = state.spectrumBoost ? 1.18 : 1;
  for (let i = 0; i < magnitude.length; i += 1) {
    const normalized = Math.log1p((magnitude[i] / max) * 90) / denominator;
    let display = Math.pow(Math.min(1.2, normalized * boost), 0.88);
    display = Math.min(1, display);
    let [red, green, blue] = colorFor(display);
    const blocked = 1 - state.combinedMask[i];
    if (blocked > 0.015) {
      const tint = Math.min(0.68, blocked * 0.72);
      red = red * (1 - tint) + 241 * tint;
      green = green * (1 - tint) + 157 * tint;
      blue = blue * (1 - tint) + 82 * tint;
    }
    const p = i * 4;
    image.data[p] = red;
    image.data[p + 1] = green;
    image.data[p + 2] = blue;
    image.data[p + 3] = 255;
  }
  context.putImageData(image, 0, 0);
}

function renderSpectrum() {
  if (!state.analysis || !state.combinedMask) return;
  const { context, width, height } = resizeCanvas(dom.spectrumCanvas);
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#030908";
  context.fillRect(0, 0, width, height);

  const spectrumSize = Math.max(120, Math.min(width - 34, height - 16));
  const x = (width - spectrumSize) / 2;
  const y = (height - spectrumSize) / 2;
  state.spectrumGeometry = { x, y, size: spectrumSize, width, height };
  paintSpectrumBuffer();
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = "high";
  context.drawImage(spectrumBuffer, x, y, spectrumSize, spectrumSize);

  context.save();
  context.strokeStyle = "rgba(170, 223, 201, 0.13)";
  context.fillStyle = "rgba(194, 222, 210, 0.38)";
  context.lineWidth = 0.7;
  context.setLineDash([2, 5]);
  const centerX = x + spectrumSize / 2;
  const centerY = y + spectrumSize / 2;
  for (const radius of [0.1, 0.2, 0.3, 0.4]) {
    const drawRadius = radius * 2 * spectrumSize;
    context.beginPath();
    context.arc(centerX, centerY, drawRadius, 0, Math.PI * 2);
    context.stroke();
  }
  context.setLineDash([]);
  context.strokeStyle = "rgba(170, 223, 201, 0.08)";
  context.beginPath();
  context.moveTo(x, centerY);
  context.lineTo(x + spectrumSize, centerY);
  context.moveTo(centerX, y);
  context.lineTo(centerX, y + spectrumSize);
  context.stroke();

  context.font = '7px "IBM Plex Mono", monospace';
  context.fillText("DC", centerX + 4, centerY + 9);
  context.fillText("0.1", x + spectrumSize * 0.86, centerY - 4);
  context.fillText("0.2", x + spectrumSize * 0.7, centerY - 4);
  context.fillText("0.3", x + spectrumSize * 0.54, centerY - 4);
  context.restore();

  for (const peak of state.peaks) {
    const selected = peak.enabled;
    const color = selected ? "#80e2bd" : peak.type === "texture" ? "#ff896f" : "#79b8d8";
    const positiveX = x + (0.5 + peak.fx) * spectrumSize;
    const positiveY = y + (0.5 + peak.fy) * spectrumSize;
    const negativeX = x + (0.5 - peak.fx) * spectrumSize;
    const negativeY = y + (0.5 - peak.fy) * spectrumSize;
    context.save();
    context.strokeStyle = selected ? "rgba(128, 226, 189, 0.42)" : "rgba(255, 137, 111, 0.23)";
    context.lineWidth = 0.7;
    context.setLineDash([2, 4]);
    context.beginPath();
    context.moveTo(positiveX, positiveY);
    context.lineTo(negativeX, negativeY);
    context.stroke();
    context.setLineDash([]);
    for (const [cx, cy, isPositive] of [
      [positiveX, positiveY, true],
      [negativeX, negativeY, false],
    ]) {
      context.beginPath();
      context.arc(cx, cy, selected ? 7 : 5.5, 0, Math.PI * 2);
      context.fillStyle = selected ? "rgba(6, 16, 14, 0.82)" : "rgba(6, 16, 14, 0.52)";
      context.fill();
      context.strokeStyle = color;
      context.lineWidth = selected ? 1.3 : 0.9;
      context.stroke();
      context.beginPath();
      context.arc(cx, cy, 1.7, 0, Math.PI * 2);
      context.fillStyle = color;
      context.fill();
      if (isPositive) {
        context.font = '6px "IBM Plex Mono", monospace';
        context.fillStyle = color;
        context.fillText(peak.code, cx + 8, cy - 6);
      }
    }
    context.restore();
  }

  if (state.painting) {
    context.save();
    context.strokeStyle = state.paintRestore ? "rgba(128,226,189,.9)" : "rgba(244,187,107,.9)";
    context.lineWidth = 1;
    context.beginPath();
    context.arc(state.lastPaint.x, state.lastPaint.y, Number(dom.brushRange.value) / 2, 0, Math.PI * 2);
    context.stroke();
    context.restore();
  }
}

function scheduleSpectrum() {
  window.cancelAnimationFrame(state.spectrumTimer);
  state.spectrumTimer = requestAnimationFrame(renderSpectrum);
}

function updateSplitFromPointer(event) {
  const rect = dom.compareStage.getBoundingClientRect();
  const next = (event.clientX - rect.left) / Math.max(1, rect.width);
  state.split = Math.max(0.035, Math.min(0.965, next));
  renderCompare();
}

dom.compareStage.addEventListener("pointerdown", (event) => {
  if (state.compareMode !== "split") return;
  dom.compareStage.setPointerCapture(event.pointerId);
  updateSplitFromPointer(event);
});

dom.compareStage.addEventListener("pointermove", (event) => {
  if (!dom.compareStage.hasPointerCapture(event.pointerId)) return;
  updateSplitFromPointer(event);
});

dom.compareStage.addEventListener("pointerup", (event) => {
  if (dom.compareStage.hasPointerCapture(event.pointerId)) {
    dom.compareStage.releasePointerCapture(event.pointerId);
  }
});

dom.compareStage.addEventListener("pointercancel", (event) => {
  if (dom.compareStage.hasPointerCapture(event.pointerId)) {
    dom.compareStage.releasePointerCapture(event.pointerId);
  }
});

$$(".view-btn").forEach((button) => {
  button.addEventListener("click", () => {
    $$(".view-btn").forEach((item) => item.classList.toggle("active", item === button));
    state.compareMode = button.dataset.view;
    renderCompare();
  });
});

function setBrushTool(restore) {
  state.paintRestore = restore;
  $("#brushEraseButton").classList.toggle("active", !restore);
  $("#brushRestoreButton").classList.toggle("active", restore);
  dom.spectrumHint.textContent = restore ? "画笔正在恢复频谱区域" : "拖动画笔，直接修整掩码";
}

$("#brushEraseButton").addEventListener("click", () => setBrushTool(false));
$("#brushRestoreButton").addEventListener("click", () => setBrushTool(true));

function spectrumPoint(event) {
  const geometry = state.spectrumGeometry;
  if (!geometry) return null;
  const rect = dom.spectrumCanvas.getBoundingClientRect();
  const localX = event.clientX - rect.left;
  const localY = event.clientY - rect.top;
  const normalizedX = (localX - geometry.x) / geometry.size;
  const normalizedY = (localY - geometry.y) / geometry.size;
  if (normalizedX < 0 || normalizedX > 1 || normalizedY < 0 || normalizedY > 1) return null;
  return {
    displayX: localX,
    displayY: localY,
    fftX: normalizedX * SIZE,
    fftY: normalizedY * SIZE,
    normalizedX,
    normalizedY,
  };
}

function paintSpectrum(event) {
  if (!state.manualMask || !state.analysis) return;
  const point = spectrumPoint(event);
  if (!point) return;
  const displayRadius = Number(dom.brushRange.value) / 2;
  const fftRadius = (displayRadius / state.spectrumGeometry.size) * SIZE;
  paintMask(state.manualMask, SIZE, point.fftX, point.fftY, fftRadius, state.paintRestore);
  invalidateLoadedMask();
  state.maskOverride = null;
  state.lastPaint = { x: point.displayX, y: point.displayY };
  state.combinedMask = combineMasks(state.autoMask, state.manualMask, SIZE);
  scheduleSpectrum();
  scheduleProcess(120);
}

dom.spectrumCanvas.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  state.painting = true;
  dom.spectrumCanvas.setPointerCapture(event.pointerId);
  paintSpectrum(event);
  event.preventDefault();
});

dom.spectrumCanvas.addEventListener("pointermove", (event) => {
  if (!state.painting) return;
  paintSpectrum(event);
});

function stopPainting(event) {
  state.painting = false;
  if (dom.spectrumCanvas.hasPointerCapture(event.pointerId)) {
    dom.spectrumCanvas.releasePointerCapture(event.pointerId);
  }
  scheduleSpectrum();
}

dom.spectrumCanvas.addEventListener("pointerup", stopPainting);
dom.spectrumCanvas.addEventListener("pointercancel", stopPainting);

$("#spectrumBoostButton").addEventListener("click", (event) => {
  state.spectrumBoost = !state.spectrumBoost;
  event.currentTarget.classList.toggle("active", state.spectrumBoost);
  renderSpectrum();
});

dom.brushRange.addEventListener("input", () => {
  dom.brushValue.textContent = `${dom.brushRange.value} px`;
});

function updateControlLabels() {
  dom.radiusValue.textContent = `${Number(dom.radiusRange.value).toFixed(1)} px`;
  dom.softnessValue.textContent = `${dom.softnessRange.value}%`;
  dom.strengthValue.textContent = `${dom.strengthRange.value}%`;
  dom.brushValue.textContent = `${dom.brushRange.value} px`;
}

[dom.radiusRange, dom.softnessRange, dom.strengthRange].forEach((input) => {
  input.addEventListener("input", () => {
    invalidateLoadedMask();
    state.maskOverride = null;
    updateControlLabels();
    scheduleProcess(55);
  });
});

$("#resetMaskButton").addEventListener("click", () => {
  invalidateLoadedMask();
  state.maskOverride = null;
  state.manualMask = newManualMask();
  state.peaks.forEach((peak) => {
    peak.enabled = peak.type === "interference";
  });
  dom.radiusRange.value = "3";
  dom.softnessRange.value = "65";
  dom.strengthRange.value = "99";
  updateControlLabels();
  renderPeakList();
  scheduleProcess(20);
  showToast("掩码已重置：仅保留三组干扰峰对");
});

$("#regenerateButton").addEventListener("click", () => {
  const seed = 120 + Math.floor(Math.random() * 8800);
  loadSample(seed);
  showToast("已生成新的自然纹理与周期干扰样本");
});

$("#selectInterferenceButton").addEventListener("click", () => {
  invalidateLoadedMask();
  state.maskOverride = null;
  state.peaks.forEach((peak) => {
    peak.enabled = peak.type === "interference";
  });
  renderPeakList();
  scheduleProcess(20);
});

$("#selectAllButton").addEventListener("click", () => {
  invalidateLoadedMask();
  state.maskOverride = null;
  state.peaks.forEach((peak) => {
    peak.enabled = true;
  });
  renderPeakList();
  scheduleProcess(20);
});

function downloadBlob(blob, filename) {
  const link = document.createElement("a");
  const url = URL.createObjectURL(blob);
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1200);
}

function maskDataUrl(mask) {
  const context = maskBuffer.getContext("2d", { willReadFrequently: true });
  const image = context.createImageData(SIZE, SIZE);
  for (let i = 0; i < mask.length; i += 1) {
    const value = Math.round(Math.max(0, Math.min(1, mask[i])) * 255);
    const p = i * 4;
    image.data[p] = value;
    image.data[p + 1] = value;
    image.data[p + 2] = value;
    image.data[p + 3] = 255;
  }
  context.putImageData(image, 0, 0);
  return maskBuffer.toDataURL("image/png");
}

$("#saveMaskButton").addEventListener("click", () => {
  if (!state.combinedMask) {
    showToast("尚无可保存的掩码", "error");
    return;
  }
  const settings = getSettings();
  const payload = {
    format: "moire-notch-mask",
    version: 1,
    createdAt: new Date().toISOString(),
    image: { width: SIZE, height: SIZE },
    settings,
    peaks: state.peaks.map(({ id, code, label, type, fx, fy, enabled, magnitude }) => ({
      id,
      code,
      label,
      type,
      fx,
      fy,
      enabled,
      magnitude,
    })),
    coverage: state.metrics?.coverage ?? 0,
    maskPng: maskDataUrl(state.combinedMask),
  };
  const blob = new Blob([JSON.stringify(payload)], { type: "application/json" });
  downloadBlob(blob, `moire-mask-${SIZE}-${Date.now()}.json`);
  showToast("频域掩码已保存为 JSON，可直接复用于同尺寸批次");
});

$("#exportButton").addEventListener("click", () => {
  if (!state.reconstructed) return;
  reconstructionCanvas.toBlob((blob) => {
    if (blob) downloadBlob(blob, `moire-reconstructed-${SIZE}.png`);
  }, "image/png");
  showToast("重建结果已导出为 PNG");
});

function loadImage(source) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("无法读取图像"));
    image.src = source;
  });
}

function isPowerOfTwo(value) {
  return value > 1 && (value & (value - 1)) === 0;
}

async function decodeMask(dataUrl, expectedWidth, expectedHeight) {
  const image = await loadImage(dataUrl);
  if (image.naturalWidth !== expectedWidth || image.naturalHeight !== expectedHeight) {
    throw new Error("掩码尺寸与 JSON 声明不一致");
  }
  if (!isPowerOfTwo(expectedWidth) || expectedWidth !== expectedHeight) {
    throw new Error("当前实验台仅支持正方形 2 的幂尺寸掩码");
  }
  const canvas = document.createElement("canvas");
  canvas.width = expectedWidth;
  canvas.height = expectedHeight;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(image, 0, 0);
  const pixels = context.getImageData(0, 0, expectedWidth, expectedHeight).data;
  const mask = new Float32Array(expectedWidth * expectedHeight);
  for (let i = 0; i < mask.length; i += 1) {
    mask[i] = pixels[i * 4] / 255;
  }
  return mask;
}

function setBatchStatus(message, type = "") {
  dom.batchStatus.className = `batch-status ${type}`.trim();
  dom.batchStatus.innerHTML = `<span class="status-dot"></span><span>${message}</span>`;
}

$("#loadMaskButton").addEventListener("click", () => dom.maskFileInput.click());

dom.maskFileInput.addEventListener("change", async () => {
  const file = dom.maskFileInput.files?.[0];
  dom.maskFileInput.value = "";
  if (!file) return;
  try {
    const payload = JSON.parse(await file.text());
    if (payload.format !== "moire-notch-mask" || payload.version !== 1) {
      throw new Error("不是有效的 Moiré Lab 掩码文件");
    }
    const width = Number(payload.image?.width);
    const height = Number(payload.image?.height);
    if (!payload.maskPng || !width || !height) {
      throw new Error("掩码文件缺少图像或数据");
    }
    const mask = await decodeMask(payload.maskPng, width, height);
    state.batchMask = mask;
    state.batchMaskSize = width;

    if (width === SIZE && height === SIZE) {
      state.maskOverride = mask;
      if (Array.isArray(payload.peaks) && payload.peaks.length) {
        state.peaks = payload.peaks.map((peak, index) => ({
          ...peak,
          id: peak.id || `loaded-${index}`,
          code: peak.code || `L${index + 1}`,
          magnitude: Number(peak.magnitude) || 1,
          enabled: Boolean(peak.enabled),
        }));
      }
      if (payload.settings) {
        dom.radiusRange.value = String(Math.max(2.5, Math.min(14, Number(payload.settings.radius) || 3)));
        dom.softnessRange.value = String(Math.max(0, Math.min(100, Math.round((Number(payload.settings.softness) || 0.65) * 100))));
        dom.strengthRange.value = String(Math.max(40, Math.min(100, Math.round((Number(payload.settings.strength) || 0.99) * 100))));
        updateControlLabels();
      }
      renderPeakList();
      processFrame();
      setBatchStatus(`掩码已载入并应用 · ${width}² · 可直接批处理`, "done");
      showToast("同尺寸掩码已载入并重放");
    } else {
      setBatchStatus(`掩码已就绪 · ${width}² · 等待同尺寸图像`, "done");
      showToast(`掩码尺寸为 ${width}²，可用于同尺寸批次`);
    }
  } catch (error) {
    setBatchStatus(error.message || "掩码载入失败");
    showToast(error.message || "掩码载入失败", "error");
  }
});

$("#batchButton").addEventListener("click", () => {
  if (!state.combinedMask) {
    showToast("请先生成或载入掩码", "error");
    return;
  }
  dom.batchFileInput.click();
});

dom.batchFileInput.addEventListener("change", async () => {
  const files = [...(dom.batchFileInput.files || [])];
  dom.batchFileInput.value = "";
  if (!files.length || state.busy) return;
  const mask = state.batchMask || state.combinedMask;
  const targetSize = state.batchMask ? state.batchMaskSize : SIZE;
  if (!mask || !targetSize) return;
  state.busy = true;
  setBatchStatus(`正在处理 0 / ${files.length} · ${targetSize}²`, "busy");
  const archiveFiles = [];
  let rejected = 0;
  try {
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      setBatchStatus(`正在处理 ${index + 1} / ${files.length} · ${file.name}`, "busy");
      await nextPaint();
      let objectUrl = "";
      try {
        objectUrl = URL.createObjectURL(file);
        const image = await loadImage(objectUrl);
        if (image.naturalWidth !== targetSize || image.naturalHeight !== targetSize) {
          rejected += 1;
          continue;
        }
        const inputCanvas = document.createElement("canvas");
        inputCanvas.width = targetSize;
        inputCanvas.height = targetSize;
        const inputContext = inputCanvas.getContext("2d", { willReadFrequently: true });
        inputContext.drawImage(image, 0, 0, targetSize, targetSize);
        const rgba = inputContext.getImageData(0, 0, targetSize, targetSize).data;
        const analysis = analyzeLuminance(rgba, targetSize);
        const result = reconstructRgba(rgba, targetSize, analysis, mask);
        const outputCanvas = document.createElement("canvas");
        outputCanvas.width = targetSize;
        outputCanvas.height = targetSize;
        const outputContext = outputCanvas.getContext("2d", { willReadFrequently: true });
        outputContext.putImageData(new ImageData(result.rgba, targetSize, targetSize), 0, 0);
        const blob = await new Promise((resolve) => outputCanvas.toBlob(resolve, "image/png"));
        if (!blob) {
          rejected += 1;
          continue;
        }
        const baseName = file.name.replace(/\.[^.]+$/, "");
        archiveFiles.push({
          name: `${baseName}_moire.png`,
          data: new Uint8Array(await blob.arrayBuffer()),
        });
      } catch {
        rejected += 1;
      } finally {
        if (objectUrl) URL.revokeObjectURL(objectUrl);
      }
    }

    if (!archiveFiles.length) {
      throw new Error(`没有可处理的 ${targetSize}² 图像`);
    }
    const zip = createZip(archiveFiles);
    downloadBlob(zip, `moire-batch-${targetSize}-${Date.now()}.zip`);
    const rejectText = rejected ? ` · 跳过 ${rejected} 张尺寸不符/失败` : "";
    setBatchStatus(`完成 ${archiveFiles.length} / ${files.length}${rejectText} · 已导出 ZIP`, "done");
    showToast(`批次完成：${archiveFiles.length} 张图像已应用同一频域掩码`);
  } catch (error) {
    setBatchStatus(error.message || "批处理失败");
    showToast(error.message || "批处理失败", "error");
  } finally {
    state.busy = false;
  }
});

const resizeObserver = new ResizeObserver(() => {
  window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(() => {
    renderCompare();
    renderSpectrum();
  }, 40);
});
resizeObserver.observe(dom.compareStage);
resizeObserver.observe(dom.spectrumCanvas);

window.addEventListener("beforeunload", () => {
  resizeObserver.disconnect();
});

updateControlLabels();
loadSample(state.seed);
