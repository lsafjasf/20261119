import {
  buildScenePair,
  FRAME_HEIGHT,
  FRAME_INTERVAL,
  FRAME_WIDTH,
  SCENE_META,
} from "./scenes.js";
import { canvasToLuma, estimateMotion, sampleField } from "./motion.js";
import {
  attachGroundTruth,
  computeMetrics,
  directionDistance,
  UNIT_DEFINITIONS,
} from "./validation.js";
import { renderEpeChart, renderField, renderFrames } from "./render.js";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

const ui = {
  frameACanvas: $("#frameACanvas"),
  frameBCanvas: $("#frameBCanvas"),
  fieldCanvas: $("#fieldCanvas"),
  fieldWrap: $("#fieldWrap"),
  selectionTooltip: $("#selectionTooltip"),
  epeChart: $("#epeChart"),
  fieldLegend: $("#fieldLegend"),
  blockSizeValue: $("#blockSizeValue"),
  searchRange: $("#searchRange"),
  searchRangeValue: $("#searchRangeValue"),
  confidenceThreshold: $("#confidenceThreshold"),
  confidenceValue: $("#confidenceValue"),
  vectorGain: $("#vectorGain"),
  vectorGainValue: $("#vectorGainValue"),
  directionEnabled: $("#directionEnabled"),
  directionCenter: $("#directionCenter"),
  directionValue: $("#directionValue"),
  groundTruthButton: $("#groundTruthButton"),
  exportButton: $("#exportButton"),
  exportMenu: $("#exportMenu"),
  modalBackdrop: $("#modalBackdrop"),
  modalBody: $("#modalBody"),
  toast: $("#toast"),
};

const state = {
  sceneId: "translation",
  scene: null,
  lumaA: null,
  lumaB: null,
  field: null,
  validation: null,
  metrics: null,
  selectedIndex: null,
  requestId: 0,
  blockSize: 24,
  search: 16,
  threshold: 0.42,
  gain: 1.2,
  directionEnabled: false,
  directionCenter: 0,
  showVectors: true,
  layer: null,
  showOcclusion: true,
  showGroundTruth: false,
};

function number(value, digits = 2) {
  return Number.isFinite(value) ? value.toFixed(digits) : "—";
}

function directionLabel(angle, coherence = 1) {
  if (!Number.isFinite(angle)) return "—";
  if (coherence < 0.42) return "多方向";
  const arrows = ["→", "↘", "↓", "↙", "←", "↖", "↑", "↗"];
  const index = Math.round(((angle % 360) + 360) % 360 / 45) % 8;
  return `${arrows[index]} ${Math.round(angle)}°`;
}

function setStatus(title, details, busy = false) {
  $("#statusText").textContent = title;
  $("#statusDetails").textContent = details;
  $(".status-dot").style.background = busy ? "var(--amber)" : "var(--cyan)";
  $(".status-dot").style.boxShadow = busy
    ? "0 0 7px rgba(255,182,95,.7)"
    : "0 0 7px rgba(77,232,210,.8)";
}

function showToast(message) {
  ui.toast.textContent = message;
  ui.toast.classList.add("is-visible");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => ui.toast.classList.remove("is-visible"), 2200);
}

function isVisible(vector) {
  if (vector.confidence < state.threshold) return false;
  if (!state.directionEnabled) return true;
  const angle = (Math.atan2(vector.vy, vector.vx) * 180 / Math.PI + 360) % 360;
  return directionDistance(angle, state.directionCenter) <= 25;
}

async function recompute() {
  const request = ++state.requestId;
  state.selectedIndex = null;
  $("#recomputeButton").classList.add("is-spinning");
  setStatus("正在估计运动场", `块匹配 ${state.blockSize}px · 搜索 ±${state.search}px`, true);
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  if (request !== state.requestId) return;

  try {
    if (!state.lumaA) state.lumaA = canvasToLuma(state.scene.frameA);
    if (!state.lumaB) state.lumaB = canvasToLuma(state.scene.frameB);
    const field = estimateMotion(state.lumaA, state.lumaB, {
      blockSize: state.blockSize,
      search: state.search,
    });
    if (request !== state.requestId) return;
    const validation = attachGroundTruth(state.scene, field, state.threshold);
    state.field = field;
    state.validation = validation;
    state.metrics = computeMetrics(field, validation, state.threshold);
    renderAll();
    setStatus(
      "分析完成",
      `${field.vectors.length} 个运动块 · 双向匹配 ${field.computeMs.toFixed(0)} ms · 真值已关联`,
    );
  } catch (error) {
    console.error(error);
    setStatus("估计失败", error.message || "未知错误");
  } finally {
    if (request === state.requestId) {
      $("#recomputeButton").classList.remove("is-spinning");
    }
  }
}

function updateSceneCopy() {
  const meta = SCENE_META[state.sceneId];
  $("#truthTitle").textContent = meta.title;
  $("#truthSubtitle").textContent = meta.subtitle;
  $("#truthDescription").textContent = meta.description;
  $$(".dataset-tab").forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.scene === state.sceneId);
    tab.setAttribute("aria-selected", String(tab.dataset.scene === state.sceneId));
  });
}

async function selectScene(sceneId) {
  state.sceneId = sceneId;
  state.scene = buildScenePair(sceneId);
  state.lumaA = null;
  state.lumaB = null;
  state.field = null;
  state.metrics = null;
  state.selectedIndex = null;
  updateSceneCopy();
  renderFrames(ui.frameACanvas, ui.frameBCanvas, state.scene);
  ui.selectionTooltip.hidden = true;
  $("#selectionEmpty").hidden = false;
  $("#selectionReadout").hidden = true;
  await recompute();
}

function fieldOptions() {
  return {
    showVectors: state.showVectors,
    layer: state.layer,
    showOcclusion: state.showOcclusion,
    showGroundTruth: state.showGroundTruth,
    threshold: state.threshold,
    gain: state.gain,
    directionEnabled: state.directionEnabled,
    directionCenter: state.directionCenter,
  };
}

function renderAll() {
  if (!state.field) return;
  renderField(ui.fieldCanvas, state.scene, state.field, fieldOptions(), state.selectedIndex);
  renderStats();
  renderSelection();
  updateFieldHud();
}

function updateFieldHud() {
  if (!state.field) return;
  const visible = state.field.vectors.filter(isVisible);
  $("#hudVectorCount").textContent = `${visible.length} VECTORS`;
  $("#hudBlockSize").textContent = `BLOCK ${state.blockSize}`;
  $("#fieldSubtitle").textContent = `块中心采样 · ${state.blockSize} px 块 · 步长 ${state.field.stride} px`;
  $("#processTime").textContent = `SAD · ${state.field.computeMs.toFixed(0)} ms`;
  $("#framePairLabel").textContent = `帧 A → 帧 B · ${SCENE_META[state.sceneId].label}`;
}

function renderStats() {
  const metrics = state.metrics;
  if (!metrics) return;
  const hasGt = state.validation?.available !== false;
  const enoughVectors = metrics.valid >= 10;
  const meanEpe = metrics.meanEpe;
  const insufficient = hasGt && (metrics.epeSamples < 10 || metrics.validRatio < 0.05);
  $("#meanEpe").textContent = hasGt && !insufficient ? number(meanEpe, 2) : "—";
  $("#p95Epe").textContent = hasGt && !insufficient ? number(metrics.p95, 2) : "—";
  $("#accuracyRate").textContent = hasGt && !insufficient && metrics.accuracyRate != null
    ? `${Math.round(metrics.accuracyRate * 100)}%`
    : "—";
  const coverage = clamp(metrics.validRatio / 0.35, 0, 1);
  const quality = hasGt && !insufficient && meanEpe != null
    ? clamp(1 - meanEpe / 3.2, 0, 1) * coverage
    : 0;
  $("#epeRing").style.setProperty("--score", (quality * 100).toFixed(1));
  let verdict = "无真值";
  if (insufficient) verdict = "低置信覆盖";
  else if (hasGt && meanEpe != null) {
    if (meanEpe < 0.35) verdict = "优秀匹配";
    else if (meanEpe < 0.8) verdict = "可靠匹配";
    else if (meanEpe < 1.5) verdict = "需要复核";
    else verdict = "显著偏差";
  }
  $("#epeVerdict").textContent = verdict;
  $("#epeVerdict").classList.toggle("is-warning", insufficient);
  $("#meanEpe").classList.toggle("is-muted", insufficient);
  $("#epeCaption").textContent = insufficient
    ? `仅 ${metrics.epeSamples} / ${metrics.total} 个高置信块 · 覆盖不足`
    : metrics.occlusionBoundaries
      ? `${metrics.epeSamples} 个高置信样本 · ${metrics.occlusionBoundaries} 块边界降权`
      : `${metrics.epeSamples} 个高置信样本 · 置信度加权`;
  $("#epeCaption").title = "EPE = |估计向量 − 已知真值向量|，单位 px。";

  $("#validVectors").textContent = metrics.valid;
  $("#validRatio").textContent = `/ ${metrics.total}`;
  $("#meanMagnitude").textContent = enoughVectors ? number(metrics.meanMagnitude, 1) : "—";
  $("#dominantDirection").textContent = enoughVectors
    ? directionLabel(metrics.dominantAngle, metrics.directionCoherence)
    : "—";
  $("#occlusionCount").textContent = metrics.occlusionBoundaries;
  $("#summaryScope").textContent = `${metrics.valid} / ${metrics.total} 高置信`;
  $("#resolutionText").textContent = `${FRAME_WIDTH} × ${FRAME_HEIGHT}`;
  $("#frameATime").textContent = "t = 0.000 s";
  $("#frameBTime").textContent = `t = ${FRAME_INTERVAL.toFixed(3)} s`;
  $("#gtBadge").textContent = hasGt ? "GT" : "N/A";
  $("#gtBadge").classList.toggle("is-muted", !hasGt);
  renderEpeChart(ui.epeChart, insufficient ? new Array(8).fill(0) : metrics.epeHistogram);
}

function renderSelection() {
  const vector = state.selectedIndex != null ? state.field?.vectors[state.selectedIndex] : null;
  $("#selectionEmpty").hidden = Boolean(vector);
  $("#selectionReadout").hidden = !vector;
  if (!vector) {
    ui.selectionTooltip.hidden = true;
    return;
  }
  const vx = vector.vx >= 0 ? `+${vector.vx.toFixed(1)}` : vector.vx.toFixed(1);
  const vy = vector.vy >= 0 ? `+${vector.vy.toFixed(1)}` : vector.vy.toFixed(1);
  $("#selectionIndex").textContent = `块 #${vector.index} · (${vector.x}, ${vector.y})`;
  $("#selectionValue").textContent = `vx ${vx} · vy ${vy} px`;
  $("#selectionMatch").textContent = Number.isFinite(vector.cost) ? vector.cost.toFixed(1) : "∞";
  $("#selectionTexture").textContent = `${Math.round(vector.textureScore * 100)}%`;
  $("#selectionFb").textContent = `${number(vector.fbError, 2)} px`;
  $("#selectionOcc").textContent = `${number(vector.occlusionWeight, 2)}`;
  ui.selectionTooltip.hidden = false;
  ui.selectionTooltip.innerHTML = `
    <strong>#${vector.index} · ${vector.x},${vector.y} px</strong>
    <span>位移 <b>${vx}, ${vy} px</b></span>
    <span>置信 <b>${(vector.confidence * 100).toFixed(0)}%</b></span>
    <span>代价 <b>${Number.isFinite(vector.cost) ? vector.cost.toFixed(1) : "∞"}</b></span>
  `;
  positionSelectionTooltip(vector);
}

function positionSelectionTooltip(vector) {
  const wrapRect = ui.fieldWrap.getBoundingClientRect();
  const scale = Math.min(wrapRect.width / FRAME_WIDTH, wrapRect.height / FRAME_HEIGHT);
  const displayWidth = FRAME_WIDTH * scale;
  const displayHeight = FRAME_HEIGHT * scale;
  const offsetX = (wrapRect.width - displayWidth) / 2;
  const offsetY = (wrapRect.height - displayHeight) / 2;
  const pixelX = offsetX + vector.x * scale;
  const pixelY = offsetY + vector.y * scale;
  const flip = pixelX + 172 > wrapRect.width;
  ui.selectionTooltip.style.left = `${pixelX}px`;
  ui.selectionTooltip.style.top = `${clamp(pixelY, 44, wrapRect.height - 34)}px`;
  ui.selectionTooltip.style.transform = flip
    ? "translate(calc(-100% - 10px), -50%)"
    : "translate(10px, -50%)";
}

function handleFieldClick(event) {
  if (!state.field) return;
  const rect = ui.fieldCanvas.getBoundingClientRect();
  const scale = Math.min(rect.width / FRAME_WIDTH, rect.height / FRAME_HEIGHT);
  const offsetX = (rect.width - FRAME_WIDTH * scale) / 2;
  const offsetY = (rect.height - FRAME_HEIGHT * scale) / 2;
  const x = (event.clientX - rect.left - offsetX) / scale;
  const y = (event.clientY - rect.top - offsetY) / scale;
  if (x < 0 || y < 0 || x > FRAME_WIDTH || y > FRAME_HEIGHT) return;
  const vector = sampleField(state.field, x, y);
  if (!vector) return;
  state.selectedIndex = vector.index;
  renderAll();
}

function updateLegend() {
  const title = ui.fieldLegend.querySelector(".legend-title");
  const ramp = ui.fieldLegend.querySelector(".legend-ramp");
  const labels = ui.fieldLegend.querySelectorAll(".legend-labels span");
  if (state.layer === "cost") {
    title.textContent = "匹配代价";
    ramp.style.background = "linear-gradient(90deg, #111d3d, #4a3780, #a44384, #ffb65f)";
    labels[0].textContent = "0";
    labels[1].textContent = "25";
    labels[2].textContent = "50+";
  } else if (state.layer === "confidence") {
    title.textContent = "置信度";
    ramp.style.background = "linear-gradient(90deg, #101a3b, #1f3f84, #1cb09d, #98e26f, #f1ffbf)";
    labels[0].textContent = "0";
    labels[1].textContent = "0.5";
    labels[2].textContent = "1.0";
  } else {
    title.textContent = "向量置信";
    ramp.style.background = "linear-gradient(90deg, #ffb65f, #4de8d2, #d8ff76)";
    labels[0].textContent = "低";
    labels[1].textContent = "中";
    labels[2].textContent = "高";
  }
}

function exportBase() {
  const meta = SCENE_META[state.sceneId];
  return {
    generator: "VectorLab Motion Field Studio",
    schema_version: "1.0",
    generated_at: new Date().toISOString(),
    scene: {
      id: state.sceneId,
      label: meta.label,
      description: meta.description,
      ground_truth: state.scene.gt,
      frame_size_px: { width: FRAME_WIDTH, height: FRAME_HEIGHT },
      frame_interval_s: FRAME_INTERVAL,
    },
    parameters: {
      block_size_px: state.blockSize,
      block_stride_px: state.field?.stride ?? null,
      search_radius_px: state.search,
      confidence_threshold: state.threshold,
      vector_display_gain: state.gain,
      direction_filter: state.directionEnabled
        ? { center_deg: state.directionCenter, half_width_deg: 25 }
        : null,
    },
    units: UNIT_DEFINITIONS,
  };
}

function exportStats() {
  if (!state.metrics) return;
  const payload = {
    ...exportBase(),
    metrics: {
      ...state.metrics,
      meanEpe_px: state.metrics.meanEpe,
      p95Epe_px: state.metrics.p95,
      allP95Epe_px: state.metrics.allP95,
      meanMagnitude_px_per_frame_pair: state.metrics.meanMagnitude,
      dominantDirection_deg_screen: state.metrics.dominantAngle,
    },
    confidence_semantics: {
      valid_if: "confidence >= confidence_threshold AND texture_score > 0.08",
      low_texture: "texture_score < 0.18",
      occlusion_downweighted: "occlusion_weight < 0.60",
      endpoint_error_scope: "ground-truth-valid blocks, confidence-weighted",
    },
  };
  downloadFile(
    `vectorlab-stats-${state.sceneId}-${timestamp()}.json`,
    JSON.stringify(payload, null, 2),
    "application/json",
  );
}

function fieldVectorRecord(vector) {
  return {
    index: vector.index,
    block_center_px: { x: vector.x, y: vector.y },
    displacement_px_per_frame_pair: { vx: vector.vx, vy: vector.vy },
    magnitude_px_per_frame_pair: Math.hypot(vector.vx, vector.vy),
    angle_deg_screen: (Math.atan2(vector.vy, vector.vx) * 180 / Math.PI + 360) % 360,
    confidence: vector.confidence,
    match_cost_gray_levels: Number.isFinite(vector.cost) ? vector.cost : null,
    texture: {
      score: vector.textureScore,
      mean_gradient: vector.meanGradient,
      variance: vector.variance,
      min_eigen: vector.minEigen,
    },
    occlusion: {
      weight: vector.occlusionWeight,
      boundary: vector.occlusionBoundary,
      fb_error_px: vector.fbError,
      neighbor_motion_delta_px: vector.neighborDiff,
    },
    ground_truth: {
      valid: vector.gtValid,
      occluded: vector.gtOccluded,
      region: vector.gtRegion,
      vx_px: vector.gtVx,
      vy_px: vector.gtVy,
      epe_px: vector.epe,
    },
    display_visible: isVisible(vector),
    valid: vector.confidence >= state.threshold && vector.textureScore > 0.08,
  };
}

function exportFieldJson() {
  if (!state.field) return;
  const payload = {
    ...exportBase(),
    field: {
      grid: {
        columns: state.field.grid.xs.length,
        rows: state.field.grid.ys.length,
        stride_px: state.field.stride,
      },
      vectors: state.field.vectors.map(fieldVectorRecord),
    },
    field_notes: [
      "Each vector originates at block_center_px in frame A and points to block_center_px + displacement in frame B.",
      "Coordinates use a top-left image origin; +x is right and +y is down.",
      "Textureless or occluded blocks retain estimates but have low confidence and/or occlusion weight.",
    ],
  };
  downloadFile(
    `vectorlab-field-${state.sceneId}-${timestamp()}.json`,
    JSON.stringify(payload, null, 2),
    "application/json",
  );
}

function exportFieldCsv() {
  if (!state.field) return;
  const header = [
    "index", "center_x_px", "center_y_px",
    "vx_px_per_frame_pair", "vy_px_per_frame_pair", "magnitude_px_per_frame_pair",
    "angle_deg_screen", "confidence_0_1", "match_cost_gray_levels", "texture_score",
    "occlusion_weight", "occlusion_boundary", "fb_error_px",
    "gt_vx_px", "gt_vy_px", "epe_px", "gt_valid", "gt_occluded", "valid", "display_visible",
  ].join(",");
  const rows = state.field.vectors.map((vector) => [
    vector.index,
    vector.x,
    vector.y,
    vector.vx,
    vector.vy,
    Math.hypot(vector.vx, vector.vy).toFixed(4),
    ((Math.atan2(vector.vy, vector.vx) * 180 / Math.PI + 360) % 360).toFixed(3),
    vector.confidence.toFixed(6),
    Number.isFinite(vector.cost) ? vector.cost.toFixed(6) : "",
    vector.textureScore.toFixed(6),
    vector.occlusionWeight.toFixed(6),
    Number(vector.occlusionBoundary),
    Number.isFinite(vector.fbError) ? vector.fbError.toFixed(4) : "",
    Number.isFinite(vector.gtVx) ? vector.gtVx.toFixed(4) : "",
    Number.isFinite(vector.gtVy) ? vector.gtVy.toFixed(4) : "",
    vector.epe != null ? vector.epe.toFixed(4) : "",
    Number(vector.gtValid),
    Number(vector.gtOccluded),
    Number(vector.confidence >= state.threshold && vector.textureScore > 0.08),
    Number(isVisible(vector)),
  ].join(","));
  const comments = [
    "# VectorLab motion field export",
    `# scene=${state.sceneId}; block_size_px=${state.blockSize}; search_radius_px=${state.search}`,
    "# coordinate_system=top-left origin, +x right, +y down",
    "# displacement_units=px per frame pair (A to B)",
    `# confidence_threshold=${state.threshold}; generated_at=${new Date().toISOString()}`,
  ];
  downloadFile(
    `vectorlab-field-${state.sceneId}-${timestamp()}.csv`,
    [...comments, header, ...rows].join("\n"),
    "text/csv;charset=utf-8",
  );
}

function exportSnapshot() {
  if (!state.field) return;
  ui.fieldCanvas.toBlob((blob) => {
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    triggerDownload(url, `vectorlab-field-${state.sceneId}-${timestamp()}.png`);
    setTimeout(() => URL.revokeObjectURL(url), 1500);
  }, "image/png");
}

function timestamp() {
  return new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
}

function downloadFile(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  triggerDownload(url, filename);
  setTimeout(() => URL.revokeObjectURL(url), 1500);
  showToast(`已导出 ${filename}`);
}

function triggerDownload(url, filename) {
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
}

function openDefinitions() {
  ui.modalBody.innerHTML = `
    <p>所有估计与导出都使用同一套坐标约定，避免在场景分析或视频增强管线中混用图像坐标与显示坐标。</p>
    <h3>坐标与运动</h3>
    <table class="unit-table">
      <tr><td>block_center_px</td><td>块中心坐标，原点是图像左上角，+x 向右，+y 向下。</td></tr>
      <tr><td>displacement</td><td>从帧 A 的块中心指向帧 B 对应位置的位移，单位 <code>px / frame pair</code>。</td></tr>
      <tr><td>angle_deg_screen</td><td>屏幕角，0° 向右，90° 向下，单位为度。</td></tr>
      <tr><td>epe_px</td><td>估计向量与已知真值向量的欧氏端点距离。</td></tr>
      <tr><td>match_cost</td><td>块内平均亮度绝对差，范围 0–255 灰度级。</td></tr>
    </table>
    <h3>置信与遮挡</h3>
    <p><code>confidence</code> 由纹理结构、匹配代价、候选歧义度与遮挡权重共同组成，范围 0–1。无纹理块保留原始估计，但会以极低置信度显示。</p>
    <p><code>occlusion_weight</code> 使用前后向一致性及相邻运动突变计算。遮挡边界处的错误向量会降低置信度并标记，但不会被伪装成精确结果。</p>
    <h3>有效性与误差</h3>
    <ul>
      <li>有效向量：<code>confidence ≥ 阈值</code> 且 <code>texture_score &gt; 0.08</code>。</li>
      <li>EPE 只统计真值有效且未被遮挡的块，并按置信度与遮挡权重加权。</li>
      <li>合成场景真值已经排除边界跨块与真实遮挡区域。</li>
    </ul>
    <h3>导出字段</h3>
    <p>JSON 包含向量、置信度、纹理、匹配代价、遮挡权重和真值；CSV 使用 <code>#</code> 注释行声明场景、块尺寸、搜索半径与单位。</p>
  `;
  ui.modalBackdrop.hidden = false;
}

function closeModal() {
  ui.modalBackdrop.hidden = true;
}

let recomputeTimer = null;
function scheduleRecompute() {
  clearTimeout(recomputeTimer);
  recomputeTimer = setTimeout(recompute, 220);
}

function bindEvents() {
  const syncRange = (input) => {
    const minimum = Number(input.min || 0);
    const maximum = Number(input.max || 100);
    const progress = ((Number(input.value) - minimum) / (maximum - minimum)) * 100;
    input.style.setProperty("--range-progress", `${clamp(progress, 0, 100)}%`);
  };
  $$(".range").forEach((input) => {
    syncRange(input);
    input.addEventListener("input", () => syncRange(input));
  });

  $$(".dataset-tab").forEach((button) => {
    button.addEventListener("click", () => selectScene(button.dataset.scene));
  });

  $$("#blockSizeControl button").forEach((button) => {
    button.addEventListener("click", () => {
      state.blockSize = Number(button.dataset.value);
      $$("#blockSizeControl button").forEach((item) => {
        item.classList.toggle("is-active", item === button);
      });
      ui.blockSizeValue.textContent = `${state.blockSize} px`;
      scheduleRecompute();
    });
  });

  ui.searchRange.addEventListener("input", () => {
    state.search = Number(ui.searchRange.value);
    ui.searchRangeValue.textContent = `${state.search} px`;
    scheduleRecompute();
  });

  ui.confidenceThreshold.addEventListener("input", () => {
    state.threshold = Number(ui.confidenceThreshold.value);
    ui.confidenceValue.textContent = state.threshold.toFixed(2);
    renderAll();
  });

  ui.vectorGain.addEventListener("input", () => {
    state.gain = Number(ui.vectorGain.value);
    ui.vectorGainValue.textContent = `${state.gain.toFixed(1)}×`;
    renderAll();
  });

  ui.directionEnabled.addEventListener("change", () => {
    state.directionEnabled = ui.directionEnabled.checked;
    ui.directionCenter.disabled = !state.directionEnabled;
    ui.directionValue.textContent = state.directionEnabled
      ? `${state.directionCenter}° ±25°`
      : "全部";
    renderAll();
  });

  ui.directionCenter.addEventListener("input", () => {
    state.directionCenter = Number(ui.directionCenter.value);
    ui.directionValue.textContent = `${state.directionCenter}° ±25°`;
    renderAll();
  });

  $$('input[data-layer]').forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const layer = checkbox.dataset.layer;
      if (layer === "vectors") {
        state.showVectors = checkbox.checked;
      } else if (layer === "occlusion") {
        state.showOcclusion = checkbox.checked;
      } else if (checkbox.checked) {
        const sibling = layer === "confidence" ? "cost" : "confidence";
        const other = $(`input[data-layer="${sibling}"]`);
        if (other) other.checked = false;
        state.layer = layer;
      } else if (state.layer === layer) {
        state.layer = null;
      }
      updateLegend();
      renderAll();
    });
  });

  ui.groundTruthButton.addEventListener("click", () => {
    state.showGroundTruth = !state.showGroundTruth;
    ui.groundTruthButton.setAttribute("aria-pressed", String(state.showGroundTruth));
    renderAll();
  });

  $("#recomputeButton").addEventListener("click", recompute);
  ui.fieldCanvas.addEventListener("click", handleFieldClick);
  window.addEventListener("resize", () => {
    const vector = state.selectedIndex != null ? state.field?.vectors[state.selectedIndex] : null;
    if (vector) positionSelectionTooltip(vector);
  });

  ui.exportButton.addEventListener("click", (event) => {
    event.stopPropagation();
    const willOpen = ui.exportMenu.hidden;
    ui.exportMenu.hidden = !willOpen;
    ui.exportButton.setAttribute("aria-expanded", String(willOpen));
  });
  ui.exportMenu.addEventListener("click", (event) => {
    const button = event.target.closest("[data-export]");
    if (!button) return;
    ui.exportMenu.hidden = true;
    ui.exportButton.setAttribute("aria-expanded", "false");
    const kind = button.dataset.export;
    if (kind === "stats-json") exportStats();
    if (kind === "field-json") exportFieldJson();
    if (kind === "field-csv") exportFieldCsv();
    if (kind === "snapshot") exportSnapshot();
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".export-wrap")) {
      ui.exportMenu.hidden = true;
      ui.exportButton.setAttribute("aria-expanded", "false");
    }
  });

  $("#helpButton").addEventListener("click", openDefinitions);
  $("#unitDetailsButton").addEventListener("click", openDefinitions);
  $("#modalClose").addEventListener("click", closeModal);
  ui.modalBackdrop.addEventListener("click", (event) => {
    if (event.target === ui.modalBackdrop) closeModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    closeModal();
    ui.exportMenu.hidden = true;
    ui.exportButton.setAttribute("aria-expanded", "false");
  });
}

bindEvents();
updateLegend();
selectScene("translation");

