import {
  cfaAt,
  demosaicBilinear,
  demosaicEdgeDirected,
  explainPixel,
  interpolationWeights,
  mosaicFromRgb,
} from "./demosaic.js";
import {
  channelErrorImage,
  computeMetrics,
  errorImage,
  rgbImage,
} from "./metrics.js";
import { createTestScene } from "./scene.js";
import { parseRawArray } from "./raw-input.js";

const CHANNEL_NAMES = { R: "R", G: "G", B: "B" };
const VIEW_LABELS = {
  reconstruct: "RGB",
  error: "ΔRGB ×7",
  fringe: "彩边残差",
  zipper: "拉链热图",
};
const state = {
  source: "synthetic",
  width: 144,
  height: 108,
  samplerPattern: "RGGB",
  declaredPattern: "RGGB",
  truth: null,
  raw: null,
  results: { bilinear: null, edge: null },
  metrics: { bilinear: null, edge: null },
  view: "reconstruct",
  errorChannel: "rgb",
  selected: { x: 73, y: 54 },
  probeMethod: "edge",
  probeChannel: "R",
  importMeta: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const dom = {
  truthCanvas: $("#truth-canvas"),
  rawCanvas: $("#raw-canvas"),
  bilinearCanvas: $("#bilinear-canvas"),
  edgeCanvas: $("#edge-canvas"),
  truthEmpty: $("#truth-empty"),
  alert: $("#pattern-alert"),
  alertText: $("#pattern-alert-text"),
  rawCfaMini: $("#raw-cfa-mini"),
  neighborhoodGrid: $("#neighborhood-grid"),
  weightList: $("#weight-list"),
  rgbReadout: $("#rgb-readout"),
  importDialog: $("#import-dialog"),
  importError: $("#import-error"),
  rawJsonInput: $("#raw-json-input"),
  toast: $("#toast"),
};

let toastTimer = 0;

function clamp(value, min, max) {
  return value < min ? min : value > max ? max : value;
}

function percent(value, digits = 2) {
  return `${(value * 100).toFixed(digits)}%`;
}

function fixed(value, digits = 2) {
  return Number.isFinite(value) ? value.toFixed(digits) : "—";
}

function setActive(container, attribute, value) {
  for (const button of $$(`[data-${attribute}]`, container)) {
    button.classList.toggle("active", button.dataset[attribute] === value);
  }
}

function showToast(message, isError = false) {
  window.clearTimeout(toastTimer);
  dom.toast.textContent = message;
  dom.toast.classList.toggle("error", isError);
  dom.toast.classList.add("visible");
  toastTimer = window.setTimeout(() => dom.toast.classList.remove("visible"), 2800);
}

function drawImage(canvas, pixels, width, height) {
  canvas.width = width;
  canvas.height = height;
  canvas.getContext("2d", { alpha: false }).putImageData(
    new ImageData(pixels, width, height),
    0,
    0,
  );
}

function drawRawImage(canvas, raw, width, height, pattern) {
  const pixels = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const i = y * width + x;
      const p = i * 4;
      const value = Math.round(clamp(raw[i], 0, 1) * 255);
      const cfa = cfaAt(pattern, x, y);
      if (cfa === "R") {
        pixels[p] = Math.min(255, value * 0.96 + 18);
        pixels[p + 1] = value * 0.78;
        pixels[p + 2] = value * 0.8;
      } else if (cfa === "G") {
        pixels[p] = value * 0.76;
        pixels[p + 1] = Math.min(255, value * 0.94 + 12);
        pixels[p + 2] = value * 0.8;
      } else {
        pixels[p] = value * 0.78;
        pixels[p + 1] = value * 0.82;
        pixels[p + 2] = Math.min(255, value * 0.96 + 18);
      }
      pixels[p + 3] = 255;
    }
  }
  drawImage(canvas, pixels, width, height);
}

function makeRawCfaMini(pattern) {
  dom.rawCfaMini.innerHTML = "";
  for (let y = 0; y < 2; y += 1) {
    for (let x = 0; x < 2; x += 1) {
      const color = cfaAt(pattern, x, y);
      const span = document.createElement("span");
      span.className = `cfa-${color.toLowerCase()}`;
      span.textContent = color;
      dom.rawCfaMini.append(span);
    }
  }
}

function updateCrosshairs() {
  const xPercent = ((state.selected.x + 0.5) / state.width) * 100;
  const yPercent = ((state.selected.y + 0.5) / state.height) * 100;
  for (const crosshair of $$(".crosshair")) {
    crosshair.style.left = `${xPercent}%`;
    crosshair.style.top = `${yPercent}%`;
  }
  $("#truth-coordinate").textContent = `x ${state.selected.x} · y ${state.selected.y}`;
}

function updateCanvasSizes() {
  const ratio = `${state.width} / ${state.height}`;
  for (const shell of $$(".canvas-shell")) shell.style.aspectRatio = ratio;
}

function viewChannelForImage() {
  return state.errorChannel === "rgb" ? "rgb" : state.errorChannel;
}

function renderViewer() {
  updateCanvasSizes();
  const { width, height } = state;
  if (state.truth) {
    drawImage(dom.truthCanvas, rgbImage(state.truth, width, height), width, height);
    dom.truthEmpty.hidden = true;
  } else {
    dom.truthEmpty.hidden = false;
    const blank = new Uint8ClampedArray(width * height * 4);
    drawImage(dom.truthCanvas, blank, width, height);
  }

  drawRawImage(dom.rawCanvas, state.raw, width, height, state.samplerPattern);
  makeRawCfaMini(state.samplerPattern);

  const view = state.truth ? state.view : "reconstruct";

  for (const method of ["bilinear", "edge"]) {
    const canvas = method === "bilinear" ? dom.bilinearCanvas : dom.edgeCanvas;
    if (view === "reconstruct") {
      drawImage(canvas, rgbImage(state.results[method], width, height), width, height);
    } else {
      const pixels = errorImage(
        state.truth,
        state.results[method],
        width,
        height,
        state.metrics[method],
        view,
        viewChannelForImage(),
      );
      drawImage(canvas, pixels, width, height);
    }
  }
  updateViewStamp();

  const selectedIndex = state.selected.y * width + state.selected.x;
  const cfa = cfaAt(state.declaredPattern, state.selected.x, state.selected.y);
  $("#raw-value-label").textContent = `${cfa} · ${fixed(state.raw[selectedIndex], 3)}`;
  $("#raw-source-pattern").textContent = `采样 ${state.samplerPattern} · 解码 ${state.declaredPattern}`;
  updateCrosshairs();
}

function decodeAndMeasure() {
  const { raw, width, height, declaredPattern, truth } = state;
  state.results.bilinear = demosaicBilinear(raw, width, height, declaredPattern);
  state.results.edge = demosaicEdgeDirected(raw, width, height, declaredPattern);
  if (truth) {
    state.metrics.bilinear = computeMetrics(truth, state.results.bilinear, width, height);
    state.metrics.edge = computeMetrics(truth, state.results.edge, width, height);
  } else {
    state.metrics.bilinear = null;
    state.metrics.edge = null;
  }
}

function renderChannelMetrics(method, metrics) {
  const container = $(`#${method}-channels`);
  if (!metrics) {
    container.innerHTML = '<p class="probe-note">导入数组没有 RGB 真值，因此不计算误差指标。</p>';
    return;
  }
  const channels = [
    ["r", "R", "channel-r"],
    ["g", "G", "channel-g"],
    ["b", "B", "channel-b"],
  ];
  container.innerHTML = channels
    .map(([key, label, className]) => {
      const value = metrics.channelMae[key];
      const width = clamp((value / 0.1) * 100, 0, 100);
      return `
        <div class="channel-metric ${className}">
          <span class="channel-label">${label}</span>
          <div class="channel-bar"><i style="width:${width}%"></i></div>
          <span class="channel-value">${percent(value)}</span>
          <canvas class="channel-map" data-channel-map="${method}-${key}"></canvas>
        </div>
      `;
    })
    .join("");
  for (const [key] of channels) {
    const canvas = $(`[data-channel-map="${method}-${key}"]`, container);
    drawImage(
      canvas,
      channelErrorImage(metrics, state.width, state.height, key),
      state.width,
      state.height,
    );
  }
}

function renderMetrics() {
  const { truth } = state;
  const bilinear = state.metrics.bilinear;
  const edge = state.metrics.edge;
  if (!truth) {
    for (const id of [
      "bilinear-psnr",
      "bilinear-mae",
      "edge-psnr",
      "edge-mae",
      "bilinear-metric-psnr",
      "bilinear-fringe",
      "bilinear-zipper",
      "bilinear-edges",
      "edge-metric-psnr",
      "edge-fringe",
      "edge-zipper",
      "edge-edges",
    ]) {
      $(`#${id}`).textContent = "—";
    }
    $("#edge-improvement").textContent = "NO TRUTH";
    $("#bilinear-footer").textContent = "无 RGB 真值";
    $("#edge-footer").textContent = "无 RGB 真值";
    renderChannelMetrics("bilinear", null);
    renderChannelMetrics("edge", null);
    return;
  }

  $("#bilinear-psnr").textContent = fixed(bilinear.psnr);
  $("#bilinear-mae").textContent = percent(bilinear.rgbMae);
  $("#edge-psnr").textContent = fixed(edge.psnr);
  $("#edge-mae").textContent = percent(edge.rgbMae);

  $("#bilinear-metric-psnr").textContent = `${fixed(bilinear.psnr)} dB`;
  $("#bilinear-fringe").textContent = `${fixed(bilinear.fringeShare, 1)}%`;
  $("#bilinear-zipper").textContent = fixed(bilinear.zipperScore);
  $("#bilinear-edges").textContent = bilinear.edgeCount.toLocaleString("en-US");
  $("#edge-metric-psnr").textContent = `${fixed(edge.psnr)} dB`;
  $("#edge-fringe").textContent = `${fixed(edge.fringeShare, 1)}%`;
  $("#edge-zipper").textContent = fixed(edge.zipperScore);
  $("#edge-edges").textContent = edge.edgeCount.toLocaleString("en-US");
  $("#edge-improvement").textContent = `MAE −${fixed((1 - edge.rgbMae / bilinear.rgbMae) * 100, 1)}%`;

  $("#bilinear-footer").textContent =
    `R ${percent(bilinear.channelMae.r)} · G ${percent(bilinear.channelMae.g)} · B ${percent(bilinear.channelMae.b)}`;
  $("#edge-footer").textContent =
    `R ${percent(edge.channelMae.r)} · G ${percent(edge.channelMae.g)} · B ${percent(edge.channelMae.b)}`;
  renderChannelMetrics("bilinear", bilinear);
  renderChannelMetrics("edge", edge);
}

function renderControls() {
  setActive($("#source-control"), "source", state.source);
  setActive($("#sensor-pattern-control"), "pattern", state.samplerPattern);
  setActive($("#declared-pattern-control"), "pattern", state.declaredPattern);
  setActive($("#view-control"), "view", state.view);
  setActive($("#error-channel-control"), "channel", state.errorChannel);
  setActive($("#probe-method-control"), "method", state.probeMethod);
  setActive($("#probe-channel-control"), "channel", state.probeChannel);

  const imported = state.source === "import";
  for (const button of $$("#sensor-pattern-control button")) button.disabled = imported;
  for (const button of $$("#view-control button")) {
    const unavailable = !state.truth && button.dataset.view !== "reconstruct";
    button.disabled = unavailable;
    button.title = unavailable ? "导入 RAW 没有 RGB 真值，无法生成误差视图" : "";
  }
  $("#channel-control").hidden = state.view !== "error" || !state.truth;

  $("#status-pill").textContent = imported ? "IMPORTED RAW" : "SYNTHETIC TRUTH";
  $("#resolution-pill").textContent = `${state.width} × ${state.height}`;
  $("#pattern-pill").textContent =
    state.samplerPattern === state.declaredPattern
      ? state.declaredPattern
      : `${state.samplerPattern}→${state.declaredPattern}`;

  const mismatch = state.samplerPattern !== state.declaredPattern;
  dom.alert.hidden = !mismatch;
  if (mismatch) {
    dom.alertText.textContent = imported
      ? `数组标记为 ${state.samplerPattern}，当前按 ${state.declaredPattern} 解码。声明不一致会系统性互换红蓝采样位置。`
      : `传感器以 ${state.samplerPattern} 采样，但两个解码器按 ${state.declaredPattern} 读取。R/B 将互换，并出现棋盘格伪彩与周期性彩边。`;
  }
}

function updateViewStamp() {
  let label = VIEW_LABELS[state.truth ? state.view : "reconstruct"];
  if (state.view === "error" && state.errorChannel !== "rgb") {
    label = `Δ${state.errorChannel.toUpperCase()} ×7`;
  }
  $("#bilinear-stamp").textContent = label;
  $("#edge-stamp").textContent = label;
}

function formatRgbValues(rgb) {
  return `
    <span class="r"><b>R</b> ${fixed(rgb.R, 3)}</span>
    <span class="g"><b>G</b> ${fixed(rgb.G, 3)}</span>
    <span class="b"><b>B</b> ${fixed(rgb.B, 3)}</span>
  `;
}

function channelDelta(truth, value) {
  if (!truth) return "—";
  return ((Math.abs(truth.R - value.R) + Math.abs(truth.G - value.G) + Math.abs(truth.B - value.B)) / 3).toFixed(4);
}

function renderRgbReadout(explanation) {
  const truth = state.truth
    ? { R: state.truth.r[state.selected.y * state.width + state.selected.x],
        G: state.truth.g[state.selected.y * state.width + state.selected.x],
        B: state.truth.b[state.selected.y * state.width + state.selected.x] }
    : null;
  dom.rgbReadout.innerHTML = `
    <div class="readout-card wide">
      <span>PIXEL · CFA · RAW</span>
      <div class="readout-values">
        <span>x ${explanation.x}</span><span>y ${explanation.y}</span>
        <span>${explanation.cfa} site</span><span>${fixed(explanation.raw, 4)}</span>
      </div>
      <div class="readout-delta">采样位置由“${state.samplerPattern}”决定，解码按“${state.declaredPattern}”解释</div>
    </div>
    <div class="readout-card wide">
      <span>${truth ? "GROUND TRUTH RGB" : "GROUND TRUTH UNAVAILABLE"}</span>
      <div class="readout-values">${truth ? formatRgbValues(truth) : "—"}</div>
    </div>
    <div class="readout-card">
      <span>BILINEAR</span>
      <div class="readout-values">${formatRgbValues(explanation.bilinear)}</div>
      <div class="readout-delta">Δavg ${channelDelta(truth, explanation.bilinear)}</div>
    </div>
    <div class="readout-card">
      <span>EDGE-DIRECTED</span>
      <div class="readout-values">${formatRgbValues(explanation.edge)}</div>
      <div class="readout-delta">Δavg ${channelDelta(truth, explanation.edge)}</div>
    </div>
  `;
}

function renderNeighborhood(weights) {
  const weightMap = new Map(weights.entries.map((term) => [`${term.x},${term.y}`, term.weight]));
  const rows = [];
  for (let dy = -3; dy <= 3; dy += 1) {
    for (let dx = -3; dx <= 3; dx += 1) {
      const x = state.selected.x + dx;
      const y = state.selected.y + dy;
      if (x < 0 || y < 0 || x >= state.width || y >= state.height) {
        rows.push(`
          <div class="sample-cell out" title="x ${x} · y ${y} · 越界，未读取">
            <div class="cell-top"><span class="sample-channel">OUT</span></div>
            <div class="cell-bottom"><span class="sample-weight">越界</span></div>
          </div>
        `);
        continue;
      }
      const index = y * state.width + x;
      const claimColor = cfaAt(state.declaredPattern, x, y);
      const sensorColor = cfaAt(state.samplerPattern, x, y);
      const weight = weightMap.get(`${x},${y}`) ?? 0;
      const selected = x === state.selected.x && y === state.selected.y;
      const weightClass = weight > 0 ? "positive" : weight < 0 ? "negative" : "";
      const weightText = Math.abs(weight) > 1e-8
        ? `${weight > 0 ? "+" : ""}${weight.toFixed(3)}`
        : "·";
      const mismatch = sensorColor !== claimColor ? " pattern-mismatch" : "";
      rows.push(`
        <div class="sample-cell cfa-${claimColor.toLowerCase()}${selected ? " selected" : ""}${weightClass ? ` ${weightClass}` : ""}${mismatch}"
             title="x ${x} · y ${y} · 解码 ${claimColor}${sensorColor !== claimColor ? ` · 实际采样 ${sensorColor}` : ""}">
          ${selected ? '<span class="target-mark" aria-hidden="true"></span>' : ""}
          <div class="cell-top">
            <span class="sample-channel ${claimColor.toLowerCase()}">${claimColor}</span>
            <span class="sample-value">${Math.round(state.raw[index] * 255)}</span>
          </div>
          <div class="cell-bottom">
            <span class="sample-coordinate">${x},${y}</span>
            <span class="sample-weight ${weightClass}">${weightText}</span>
          </div>
        </div>
      `);
    }
  }
  dom.neighborhoodGrid.innerHTML = rows.join("");
}

function renderWeightList(weights) {
  if (!weights.entries.length) {
    dom.weightList.innerHTML = '<li><span>—</span><span>无可用的同色采样</span><span>—</span></li>';
    return;
  }
  dom.weightList.innerHTML = weights.entries
    .slice(0, 12)
    .map((term, index) => {
      const cfa = cfaAt(state.declaredPattern, term.x, term.y);
      return `
        <li>
          <span class="weight-index">${String(index + 1).padStart(2, "0")}</span>
          <span class="weight-source">x ${term.x} · y ${term.y} · ${cfa}</span>
          <span class="weight-number ${term.weight < 0 ? "negative" : ""}">
            ${term.weight > 0 ? "+" : ""}${term.weight.toFixed(4)}
          </span>
        </li>
      `;
    })
    .join("");
}

function renderProbe() {
  const { x, y } = state.selected;
  const explanation = explainPixel(
    state.raw,
    state.width,
    state.height,
    state.declaredPattern,
    x,
    y,
  );
  const weights = interpolationWeights(
    state.raw,
    state.width,
    state.height,
    state.declaredPattern,
    x,
    y,
    state.probeMethod,
    state.probeChannel,
  );

  $("#probe-coordinate").textContent = `x ${x} · y ${y}`;
  $("#probe-cfa").textContent = `${explanation.cfa} site`;
  $("#probe-raw-value").textContent = `raw ${fixed(explanation.raw, 4)}`;
  $("#weight-sum").textContent = `Σw = ${weights.sum.toFixed(4)}`;
  $("#weight-channel-label").textContent = `${state.probeChannel} 通道`;
  renderRgbReadout(explanation);
  renderNeighborhood(weights);
  renderWeightList(weights);

  const methodText = state.probeMethod === "edge"
    ? "边缘导向先按水平/垂直梯度估计 G，再以 G 为基准传播 R/B。负权重用于抑制边缘彩边，因此采样可能反向贡献。"
    : "双线性仅平均声明 CFA 下的同色样本，权重为正且均匀。图像边缘只使用存在的样本，不进行越界读取。";
  const mismatchText = state.samplerPattern === state.declaredPattern
    ? ""
    : ` 当前邻域按解码声明 ${state.declaredPattern} 标色；传感器实际采样为 ${state.samplerPattern}。`;
  $("#probe-note").textContent = methodText + mismatchText;
}

function renderAll() {
  renderControls();
  renderViewer();
  renderMetrics();
  renderProbe();
}

function resetSelection() {
  state.selected = {
    x: Math.min(state.width - 1, Math.floor(state.width / 2) + (state.width > 2 ? 1 : 0)),
    y: Math.floor(state.height / 2),
  };
}

function generateSynthetic({ resetPosition = false } = {}) {
  state.source = "synthetic";
  state.width = 144;
  state.height = 108;
  state.truth = createTestScene(state.width, state.height);
  state.raw = mosaicFromRgb(state.truth, state.width, state.height, state.samplerPattern);
  state.importMeta = null;
  if (resetPosition) resetSelection();
  decodeAndMeasure();
  renderAll();
}

function loadRawArray(imported) {
  state.source = "import";
  state.width = imported.width;
  state.height = imported.height;
  state.raw = imported.raw;
  state.truth = null;
  state.samplerPattern = imported.pattern;
  state.declaredPattern = imported.pattern;
  state.importMeta = imported;
  state.view = "reconstruct";
  state.errorChannel = "rgb";
  resetSelection();
  decodeAndMeasure();
  renderAll();
  showToast(
    `已载入 ${imported.width} × ${imported.height} RAW 数组 · ${imported.pattern} · 缩放 1/${imported.scale}`,
  );
}

function selectPixel(x, y) {
  state.selected.x = clamp(x, 0, state.width - 1);
  state.selected.y = clamp(y, 0, state.height - 1);
  renderViewer();
  renderProbe();
}

function downloadRawJson() {
  const data = Array.from(state.raw, (value) => Math.round(value * 255));
  const payload = {
    pattern: state.samplerPattern,
    width: state.width,
    height: state.height,
    maxValue: 255,
    data,
  };
  const blob = new Blob([JSON.stringify(payload)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `bayer-${state.declaredPattern}-${state.width}x${state.height}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
  showToast(`已导出 ${state.width * state.height} 个单通道样本`);
}

function bindCanvasInteraction() {
  for (const shell of $$(".canvas-shell")) {
    const canvas = $("canvas", shell);
    canvas.tabIndex = 0;
    canvas.title = "点击选择像素；方向键移动，Shift + 方向键一次移动 10 像素";
    const pick = (event) => {
      const rect = canvas.getBoundingClientRect();
      const x = Math.floor(((event.clientX - rect.left) / rect.width) * state.width);
      const y = Math.floor(((event.clientY - rect.top) / rect.height) * state.height);
      selectPixel(x, y);
    };
    shell.addEventListener("click", pick);
    canvas.addEventListener("keydown", (event) => {
      const step = event.shiftKey ? 10 : 1;
      let { x, y } = state.selected;
      if (event.key === "ArrowLeft") x -= step;
      else if (event.key === "ArrowRight") x += step;
      else if (event.key === "ArrowUp") y -= step;
      else if (event.key === "ArrowDown") y += step;
      else return;
      event.preventDefault();
      selectPixel(x, y);
    });
  }
}

function openImportDialog() {
  dom.importError.hidden = true;
  dom.importError.textContent = "";
  if (!dom.importDialog.open) dom.importDialog.showModal();
  window.setTimeout(() => dom.rawJsonInput.focus(), 30);
}

function bindControls() {
  $("#source-control").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-source]");
    if (!button) return;
    if (button.dataset.source === "synthetic") {
      generateSynthetic();
    } else {
      openImportDialog();
    }
  });

  $("#sensor-pattern-control").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-pattern]");
    if (!button || button.disabled || state.source !== "synthetic") return;
    state.samplerPattern = button.dataset.pattern;
    state.raw = mosaicFromRgb(state.truth, state.width, state.height, state.samplerPattern);
    decodeAndMeasure();
    renderAll();
  });

  $("#declared-pattern-control").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-pattern]");
    if (!button || button.dataset.pattern === state.declaredPattern) return;
    state.declaredPattern = button.dataset.pattern;
    decodeAndMeasure();
    renderAll();
  });

  $("#view-control").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-view]");
    if (!button || button.disabled) return;
    state.view = button.dataset.view;
    renderControls();
    renderViewer();
  });

  $("#error-channel-control").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-channel]");
    if (!button) return;
    state.errorChannel = button.dataset.channel;
    renderControls();
    renderViewer();
  });

  $("#probe-method-control").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-method]");
    if (!button) return;
    state.probeMethod = button.dataset.method;
    renderControls();
    renderProbe();
  });

  $("#probe-channel-control").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-channel]");
    if (!button) return;
    state.probeChannel = button.dataset.channel;
    renderControls();
    renderProbe();
  });

  $("#fix-pattern-button").addEventListener("click", () => {
    state.declaredPattern = state.samplerPattern;
    decodeAndMeasure();
    renderAll();
    showToast(`解码声明已修正为 ${state.declaredPattern}`);
  });

  $("#regenerate-button").addEventListener("click", () => {
    generateSynthetic({ resetPosition: true });
    showToast("测试场景已重新生成：斜线、彩色细条与饱和亮点");
  });

  $("#import-button").addEventListener("click", openImportDialog);
  $("#export-button").addEventListener("click", downloadRawJson);

  $("#raw-file-input").addEventListener("change", async (event) => {
    const [file] = event.target.files;
    if (!file) return;
    try {
      dom.rawJsonInput.value = await file.text();
      dom.importError.hidden = true;
    } catch (error) {
      dom.importError.textContent = `读取文件失败：${error.message}`;
      dom.importError.hidden = false;
    }
  });

  $("#load-raw-button").addEventListener("click", () => {
    try {
      const imported = parseRawArray(dom.rawJsonInput.value);
      loadRawArray(imported);
      dom.importDialog.close();
    } catch (error) {
      dom.importError.textContent = error.message;
      dom.importError.hidden = false;
    }
  });

  dom.importDialog.addEventListener("close", () => {
    dom.importError.hidden = true;
    $("#raw-file-input").value = "";
  });
}

function init() {
  bindCanvasInteraction();
  bindControls();
  generateSynthetic();
}

init();
