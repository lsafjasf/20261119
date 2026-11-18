import { exportEffectWav } from "./export.js";
import { formatLabel } from "./pcm.js";
import { ReversePlayer } from "./player.js";
import { mapSourceFrameToOutput, normalizeRegion } from "./reverse.js";
import { blobSource, parseWav } from "./wav.js";
import { buildWaveform } from "./waveform.js";

const $ = (selector) => document.querySelector(selector);
const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
const numberFormat = new Intl.NumberFormat("en-US");

const elements = {
  deck: $(".deck"),
  fileInput: $("#file-input"),
  browseButton: $("#browse-button"),
  stageEmpty: $("#stage-empty"),
  fileName: $("#file-name"),
  statusText: $("#status-text"),
  statusDot: $("#status-dot"),
  footerFormat: $("#footer-format"),
  formatReadout: $("#format-readout"),
  waveStage: $(".wave-stage"),
  waveform: $("#waveform"),
  overlay: $("#wave-overlay"),
  scanProgress: $("#scan-progress"),
  scanLabel: $("#scan-label"),
  scanFill: $("#scan-fill"),
  regionSummary: $("#region-summary"),
  waveHint: $("#wave-hint"),
  outputTime: $("#output-time"),
  sourceTime: $("#source-time"),
  sourceFrame: $("#source-frame"),
  outputProgress: $("#output-progress-fill"),
  directionPill: $("#direction-pill"),
  directionLabel: $("#direction-label"),
  playButton: $("#play-button"),
  rewindButton: $("#rewind-button"),
  exportButton: $("#export-button"),
  modeButtons: [...document.querySelectorAll(".mode-button")],
  regionControls: $("#region-controls"),
  selStart: $("#sel-start"),
  selEnd: $("#sel-end"),
  selStartTime: $("#sel-start-time"),
  selEndTime: $("#sel-end-time"),
  regionLength: $("#region-length"),
  selectionFrames: $("#selection-frames"),
  blockSize: $("#block-size"),
  prefetchBlocks: $("#prefetch-blocks"),
  loopToggle: $("#loop-toggle"),
  prefetchPeak: $("#prefetch-peak"),
  heldMemory: $("#held-memory"),
  scheduledMemory: $("#scheduled-memory"),
  fileSize: $("#file-size"),
  frameTotal: $("#frame-total"),
  memoryFill: $("#memory-fill"),
  memoryNote: $("#memory-note"),
  toast: $("#toast"),
};

const state = {
  source: null,
  format: null,
  fileName: "",
  fileSize: 0,
  mode: "full",
  selection: { startFrame: 0, endFrame: 0 },
  savedSelection: null,
  waveform: null,
  scanController: null,
  loadToken: 0,
  drag: null,
  hoverFrame: null,
  cursorSourceFrame: 0,
  canvasWidth: 1,
  canvasHeight: 1,
  busy: false,
};

const player = new ReversePlayer({
  onCursor: renderCursor,
  onState: renderState,
  onError: (error) => showToast(error.message || String(error), true),
});

function formatTime(frames) {
  if (!state.format) return "00:00.000";
  const safeFrames = Math.max(0, frames);
  const totalMilliseconds = Math.round((safeFrames / state.format.sampleRate) * 1000);
  const minutes = Math.floor(totalMilliseconds / 60_000);
  const seconds = Math.floor((totalMilliseconds % 60_000) / 1000);
  const milliseconds = totalMilliseconds % 1000;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(milliseconds).padStart(3, "0")}`;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 KiB";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GiB`;
}

let toastTimer = 0;
function showToast(message, isError = false) {
  clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.toggle("is-error", isError);
  elements.toast.hidden = false;
  toastTimer = setTimeout(() => {
    elements.toast.hidden = true;
  }, 5200);
}

function setStatus(text, live = false) {
  elements.statusText.textContent = text;
  elements.statusDot.classList.toggle("is-live", live);
}

function setControlsEnabled(enabled) {
  elements.playButton.disabled = !enabled;
  elements.rewindButton.disabled = !enabled;
  elements.exportButton.disabled = !enabled;
  elements.selStart.disabled = !enabled || state.mode !== "region";
  elements.selEnd.disabled = !enabled || state.mode !== "region";
  elements.blockSize.disabled = !enabled;
  elements.prefetchBlocks.disabled = !enabled;
  elements.loopToggle.disabled = !enabled;
}

async function loadFile(file) {
  if (!file) return;
  const token = ++state.loadToken;
  state.scanController?.abort();
  state.waveform = null;
  state.busy = true;
  setControlsEnabled(false);
  setStatus("读取 WAV 头…");
  elements.scanProgress.hidden = true;

  try {
    const source = blobSource(file);
    const format = await parseWav(source);
    if (token !== state.loadToken) return;
    if (format.frames < 1) throw new Error("WAV 中没有完整的 PCM 帧。");

    player.stop();
    state.source = source;
    state.format = format;
    state.fileName = file.name;
    state.fileSize = file.size;
    state.mode = "full";
    state.selection = normalizeRegion(null, format.frames);
    state.savedSelection = null;
    state.cursorSourceFrame = format.frames - 1;

    player.setAudio(source, format);
    player.configure({
      region: state.selection,
      blockFrames: Number(elements.blockSize.value),
      prefetchBlocks: Number(elements.prefetchBlocks.value),
      loop: elements.loopToggle.checked,
    });

    updateFileMetadata();
    updateModeUI();
    updateSelectionUI();
    setControlsEnabled(true);
    elements.stageEmpty.hidden = true;
    elements.deck.classList.remove("is-playing");
    setStatus("PCM 已就绪 · 正在扫描波形", true);
    drawBase();
    drawOverlay();
    state.busy = false;
    void scanWaveform(token);
  } catch (error) {
    if (token !== state.loadToken) return;
    state.busy = false;
    setControlsEnabled(Boolean(state.format));
    setStatus("WAV 载入失败");
    showToast(error.message || String(error), true);
  }
}

function updateFileMetadata() {
  const format = state.format;
  elements.fileName.textContent = state.fileName || "NO TAPE LOADED";
  elements.formatReadout.title = formatLabel(format);
  elements.footerFormat.textContent = `${formatLabel(format)} · ${formatTime(format.frames)}`;
  elements.fileSize.textContent = formatBytes(state.fileSize);
  elements.frameTotal.textContent = numberFormat.format(format.frames);
  elements.waveStage.style.setProperty("--duration", format.duration);
  elements.selStart.max = String(format.frames);
  elements.selEnd.max = String(format.frames);
  elements.selStart.value = "0";
  elements.selEnd.value = String(format.frames);
  updateRegionCopy();
}

async function scanWaveform(token) {
  state.scanController?.abort();
  const controller = new AbortController();
  state.scanController = controller;
  elements.scanProgress.hidden = false;
  elements.scanLabel.textContent = "扫描波形 0%";
  elements.scanFill.style.width = "0%";

  try {
    const waveform = await buildWaveform(state.source, state.format, {
      buckets: 2600,
      blockFrames: 65536,
      signal: controller.signal,
      onProgress(progress) {
        if (token !== state.loadToken) return;
        const percent = Math.round(progress * 100);
        elements.scanLabel.textContent = `扫描波形 ${percent}%`;
        elements.scanFill.style.width = `${percent}%`;
        setStatus(`PCM 已就绪 · 扫描波形 ${percent}%`, true);
      },
    });
    if (token !== state.loadToken) return;
    state.waveform = waveform;
    elements.scanProgress.hidden = true;
    setStatus("PCM 已就绪 · 分块试听", true);
    drawBase();
    drawOverlay();
  } catch (error) {
    if (error?.name === "AbortError" || token !== state.loadToken) return;
    elements.scanProgress.hidden = true;
    showToast(`波形扫描失败：${error.message}`, true);
  }
}

const waveContext = elements.waveform.getContext("2d");
const overlayContext = elements.overlay.getContext("2d");

function resizeCanvases() {
  const rect = elements.waveStage.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(1, Math.round(rect.width));
  const height = Math.max(1, Math.round(rect.height));
  if (width === state.canvasWidth && height === state.canvasHeight) return;
  state.canvasWidth = width;
  state.canvasHeight = height;
  for (const canvas of [elements.waveform, elements.overlay]) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
  }
  waveContext.setTransform(dpr, 0, 0, dpr, 0, 0);
  overlayContext.setTransform(dpr, 0, 0, dpr, 0, 0);
  drawBase();
  drawOverlay();
}

function drawGrid(context, width, height) {
  context.save();
  context.strokeStyle = "rgba(242, 236, 223, 0.055)";
  context.lineWidth = 1;
  for (let x = 80; x < width; x += 80) {
    context.beginPath();
    context.moveTo(x + 0.5, 0);
    context.lineTo(x + 0.5, height);
    context.stroke();
  }
  for (let y = 40; y < height; y += 40) {
    context.beginPath();
    context.moveTo(0, y + 0.5);
    context.lineTo(width, y + 0.5);
    context.stroke();
  }
  context.restore();
}

function drawSelection(context, width, height) {
  if (!state.format) return;
  const total = state.format.frames;
  if (total <= 0) return;
  const startX = (state.selection.startFrame / total) * width;
  const endX = (state.selection.endFrame / total) * width;
  context.save();
  context.fillStyle =
    state.mode === "region" ? "rgba(255, 139, 92, 0.085)" : "rgba(139, 216, 194, 0.035)";
  context.fillRect(startX, 0, Math.max(1, endX - startX), height);
  if (state.mode === "region") {
    context.strokeStyle = "rgba(255, 139, 92, 0.7)";
    context.setLineDash([4, 5]);
    context.beginPath();
    context.moveTo(startX + 0.5, 0);
    context.lineTo(startX + 0.5, height);
    context.moveTo(endX - 0.5, 0);
    context.lineTo(endX - 0.5, height);
    context.stroke();
    context.setLineDash([]);
    context.fillStyle = "rgba(255, 139, 92, 0.8)";
    context.fillRect(startX, 0, 2, 10);
    context.fillRect(endX - 2, 0, 2, 10);
  }
  context.restore();
}

function drawBase() {
  const width = state.canvasWidth;
  const height = state.canvasHeight;
  waveContext.clearRect(0, 0, width, height);
  drawGrid(waveContext, width, height);
  if (!state.format) return;

  const channels = state.format.channels;
  const lanes = Math.min(channels, 3);
  const laneHeight = height / lanes;
  const waveform = state.waveform;
  if (!waveform) {
    waveContext.save();
    waveContext.fillStyle = "rgba(242, 236, 223, 0.28)";
    waveContext.font = '10px "Courier New", monospace';
    waveContext.textAlign = "center";
    waveContext.fillText("READING PCM BLOCKS…", width / 2, height / 2);
    waveContext.restore();
    return;
  }

  const colors = [
    "rgba(242, 236, 223, 0.62)",
    "rgba(139, 216, 194, 0.72)",
    "rgba(255, 139, 92, 0.58)",
  ];
  for (let lane = 0; lane < lanes; lane += 1) {
    const centerY = laneHeight * lane + laneHeight / 2;
    const amplitude = laneHeight * 0.36;
    waveContext.save();
    waveContext.strokeStyle = "rgba(242, 236, 223, 0.08)";
    waveContext.beginPath();
    waveContext.moveTo(0, centerY + 0.5);
    waveContext.lineTo(width, centerY + 0.5);
    waveContext.stroke();
    waveContext.strokeStyle = colors[lane];
    waveContext.lineWidth = 1;
    waveContext.beginPath();
    const step = width / waveform.buckets;
    for (let bucket = 0; bucket < waveform.buckets; bucket += 1) {
      const x = (bucket + 0.5) * step;
      const minimum = waveform.minima[lane][bucket];
      const maximum = waveform.maxima[lane][bucket];
      waveContext.moveTo(x, centerY - maximum * amplitude);
      waveContext.lineTo(x, centerY - minimum * amplitude);
    }
    waveContext.stroke();
    waveContext.fillStyle = "rgba(242, 236, 223, 0.22)";
    waveContext.font = '8px "Courier New", monospace';
    waveContext.fillText(`CH ${lane + 1}`, 10, laneHeight * lane + 14);
    waveContext.restore();
  }
  drawSelection(waveContext, width, height);
}

function drawOverlay() {
  const width = state.canvasWidth;
  const height = state.canvasHeight;
  overlayContext.clearRect(0, 0, width, height);
  if (!state.format) return;
  const total = state.format.frames;
  if (total <= 0) return;

  if (state.hoverFrame != null && !state.drag) {
    const hoverX = (state.hoverFrame / total) * width;
    overlayContext.strokeStyle = "rgba(242, 236, 223, 0.16)";
    overlayContext.lineWidth = 1;
    overlayContext.beginPath();
    overlayContext.moveTo(hoverX + 0.5, 0);
    overlayContext.lineTo(hoverX + 0.5, height);
    overlayContext.stroke();
  }

  const sourceFrame = state.cursorSourceFrame;
  const x = (sourceFrame / total) * width;
  overlayContext.save();
  overlayContext.shadowColor = "rgba(255, 139, 92, 0.65)";
  overlayContext.shadowBlur = 12;
  overlayContext.strokeStyle = "#ff8b5c";
  overlayContext.lineWidth = 1.5;
  overlayContext.beginPath();
  overlayContext.moveTo(x + 0.5, 0);
  overlayContext.lineTo(x + 0.5, height);
  overlayContext.stroke();
  overlayContext.shadowBlur = 0;
  overlayContext.fillStyle = "#ff8b5c";
  overlayContext.beginPath();
  overlayContext.moveTo(x - 5, 0);
  overlayContext.lineTo(x + 5, 0);
  overlayContext.lineTo(x, 9);
  overlayContext.closePath();
  overlayContext.fill();
  overlayContext.restore();
}

function frameAtPointer(event) {
  const rect = elements.waveStage.getBoundingClientRect();
  const ratio = clamp((event.clientX - rect.left) / Math.max(1, rect.width), 0, 1);
  return clamp(Math.floor(ratio * state.format.frames), 0, state.format.frames - 1);
}

function seekToSourceFrame(sourceFrame) {
  if (!state.format) return;
  const outputFrame = mapSourceFrameToOutput(
    sourceFrame,
    state.format.frames,
    player.region,
  );
  void player.seek(outputFrame);
}

function handlePointerDown(event) {
  if (!state.format || event.button !== 0) return;
  const frame = frameAtPointer(event);
  if (state.mode !== "region") {
    seekToSourceFrame(frame);
    return;
  }
  if (player.playing) player.pause();
  state.drag = { anchor: frame, frame };
  elements.waveStage.setPointerCapture?.(event.pointerId);
  drawBase();
  drawOverlay();
}

function handlePointerMove(event) {
  if (!state.format) return;
  const frame = frameAtPointer(event);
  state.hoverFrame = frame;
  if (state.drag) {
    state.drag.frame = frame;
    const start = Math.min(state.drag.anchor, frame);
    const end = Math.max(state.drag.anchor, frame) + 1;
    state.selection = {
      startFrame: clamp(start, 0, state.format.frames - 1),
      endFrame: clamp(end, 1, state.format.frames),
    };
    updateSelectionUI();
    drawBase();
  }
  drawOverlay();
}

function handlePointerUp(event) {
  if (!state.format) return;
  if (!state.drag) return;
  const drag = state.drag;
  state.drag = null;
  elements.waveStage.releasePointerCapture?.(event.pointerId);
  if (drag.anchor === drag.frame) {
    seekToSourceFrame(drag.frame);
    drawOverlay();
    return;
  }
  applySelection(state.selection.startFrame, state.selection.endFrame, true);
}

function handlePointerCancel() {
  state.drag = null;
  updateSelectionUI();
  drawBase();
  drawOverlay();
}

function defaultRegion() {
  const total = state.format.frames;
  if (total < 2) return { startFrame: 0, endFrame: total };
  return {
    startFrame: Math.floor(total * 0.25),
    endFrame: Math.max(Math.floor(total * 0.75), Math.floor(total * 0.25) + 1),
  };
}

function updateRegionCopy() {
  if (!state.format) return;
  const { startFrame, endFrame } = state.selection;
  const frameCount = endFrame - startFrame;
  elements.selStartTime.textContent = formatTime(startFrame);
  elements.selEndTime.textContent = formatTime(endFrame);
  elements.selectionFrames.textContent = `${numberFormat.format(frameCount)} frames`;
  elements.regionLength.textContent =
    state.mode === "full" ? "整段" : (frameCount / state.format.sampleRate).toFixed(3) + " s";
  if (state.mode === "full") {
    elements.regionSummary.textContent = `整段倒放 · ${formatTime(state.format.frames)}`;
  } else {
    elements.regionSummary.textContent =
      `区间 ${formatTime(startFrame)} → ${formatTime(endFrame)} · ` +
      `${numberFormat.format(frameCount)} frames`;
  }
}

function updateSelectionUI() {
  if (!state.format) return;
  const { startFrame, endFrame } = state.selection;
  elements.selStart.value = String(startFrame);
  elements.selEnd.value = String(endFrame);
  elements.selStart.disabled = state.mode !== "region";
  elements.selEnd.disabled = state.mode !== "region";
  elements.regionControls.classList.toggle("is-disabled", state.mode !== "region");
  elements.waveHint.textContent =
    state.mode === "region"
      ? "拖动波形选择区间 · 单击定位"
      : "波形显示源位置 · 游标由右向左";
  updateRegionCopy();
}

function updateModeUI() {
  for (const button of elements.modeButtons) {
    button.classList.toggle("is-active", button.dataset.mode === state.mode);
  }
  updateSelectionUI();
}

function applySelection(startFrame, endFrame, seek = false) {
  if (!state.format) return;
  const total = state.format.frames;
  let start = clamp(Math.floor(startFrame), 0, total - 1);
  let end = clamp(Math.floor(endFrame), start + 1, total);
  if (start >= end) {
    start = Math.max(0, end - 1);
  }
  state.selection = { startFrame: start, endFrame: end };
  if (state.mode === "region") {
    state.savedSelection = { ...state.selection };
    if (player.playing) player.pause();
    player.configure({ region: state.selection });
    updateSelectionUI();
    drawBase();
    drawOverlay();
    if (seek) void player.seek(start);
  }
}

function setMode(mode) {
  if (!state.format || mode === state.mode) return;
  if (player.playing) player.pause();
  if (state.mode === "region") {
    state.savedSelection = { ...state.selection };
  }
  state.mode = mode;
  if (mode === "region") {
    state.selection = state.savedSelection
      ? { ...state.savedSelection }
      : defaultRegion();
  } else {
    state.selection = { startFrame: 0, endFrame: state.format.frames };
  }
  player.configure({ region: state.selection });
  updateModeUI();
  drawBase();
  drawOverlay();
  void player.seek(state.mode === "region" ? state.selection.startFrame : 0);
}

function handleSliderInput(event) {
  if (!state.format) return;
  let start = Number(elements.selStart.value);
  let end = Number(elements.selEnd.value);
  if (start >= end) {
    if (event.target === elements.selStart) {
      start = Math.max(0, end - 1);
    } else {
      end = Math.min(state.format.frames, start + 1);
    }
  }
  applySelection(start, end, false);
}

function renderState(playbackState) {
  const playing = Boolean(playbackState?.playing);
  elements.playButton.classList.toggle("is-playing", playing);
  elements.playButton.setAttribute("aria-label", playing ? "暂停" : "播放");
  elements.deck.classList.toggle("is-playing", playing);
  if (playing) {
    setStatus("正在分块倒放试听", true);
  } else if (state.format && !state.busy) {
    setStatus("PCM 已就绪 · 分块试听", true);
  }
}

function renderCursor(cursor) {
  if (!state.format) return;
  const total = state.format.frames;
  state.cursorSourceFrame = cursor.sourceFrame;
  elements.outputTime.textContent = formatTime(Math.min(total, cursor.outputFrame));
  elements.sourceTime.textContent = formatTime(cursor.sourceFrame);
  elements.sourceFrame.textContent = numberFormat.format(cursor.sourceFrame);
  elements.outputProgress.style.width = `${clamp((cursor.outputFrame / total) * 100, 0, 100)}%`;
  elements.playButton.classList.toggle("is-playing", cursor.playing);
  elements.deck.classList.toggle("is-playing", cursor.playing);
  elements.prefetchPeak.textContent = formatBytes(cursor.prefetchPeakBytes);
  elements.heldMemory.textContent = formatBytes(cursor.heldBytes);
  elements.scheduledMemory.textContent = formatBytes(cursor.scheduledAudioBytes);
  elements.fileSize.textContent = formatBytes(state.fileSize);

  const inReverseRegion =
    cursor.outputFrame >= player.region.startFrame &&
    cursor.outputFrame < player.region.endFrame;
  const reverse = inReverseRegion && player.region.endFrame > player.region.startFrame;
  elements.directionPill.classList.toggle("is-forward", !reverse);
  elements.directionPill.querySelector(".direction-arrow").textContent = reverse ? "←" : "→";
  elements.directionLabel.textContent = reverse ? "REVERSE" : "FORWARD";

  const capacity =
    state.format.blockAlign * Number(elements.blockSize.value) *
      (Number(elements.prefetchBlocks.value) + 1) +
    state.format.sampleRate * state.format.channels * 4 * 2;
  const used = cursor.prefetchPeakBytes + cursor.peakScheduledAudioBytes;
  elements.memoryFill.style.width = `${clamp((used / capacity) * 100, 0, 100)}%`;
  elements.memoryNote.textContent =
    `预读只持有少量块；播放窗口约 ${formatBytes(cursor.peakScheduledAudioBytes)}。` +
    "不会构建整段输出缓冲。";
  drawOverlay();
}

async function togglePlayback() {
  if (!state.format) return;
  if (player.playing) {
    player.pause();
  } else {
    await player.play(player.currentFrame);
  }
}

function rewind() {
  if (!state.format) return;
  if (player.playing) player.pause();
  void player.seek(0);
}

function reconfigurePlayback() {
  if (!state.format) return;
  const wasPlaying = player.playing;
  if (wasPlaying) player.pause();
  player.configure({
    blockFrames: Number(elements.blockSize.value),
    prefetchBlocks: Number(elements.prefetchBlocks.value),
    loop: elements.loopToggle.checked,
  });
  player.meter.resetPeak();
  if (wasPlaying) void player.play(player.currentFrame);
}

function suggestedFileName() {
  const base = (state.fileName || "tape").replace(/\.wav$/i, "");
  return `${base}${state.mode === "region" ? "-region-reverse" : "-reverse"}.wav`;
}

async function handleExport() {
  if (!state.format || state.busy) return;
  if (state.format.dataLength > 0xffffffff - 36) {
    showToast("输出超过 RIFF/WAV 的 4 GiB 限制。", true);
    return;
  }
  if (player.playing) player.pause();
  state.busy = true;
  elements.exportButton.disabled = true;
  setStatus("流式导出 0%");
  try {
    const result = await exportEffectWav({
      source: state.source,
      format: state.format,
      region: player.region,
      blockFrames: Number(elements.blockSize.value),
      prefetchBlocks: Number(elements.prefetchBlocks.value),
      suggestedName: suggestedFileName(),
      meter: player.meter,
      onProgress(progress) {
        setStatus(`流式导出 ${Math.round(progress * 100)}%`, true);
      },
    });
    if (result.cancelled) {
      showToast("已取消导出。");
    } else if (result.streamed) {
      showToast(`已流式写入 ${formatBytes(result.bytes)}，未构建整段内存副本。`);
    } else {
      showToast(
        `当前浏览器不支持流式写盘，已通过 Blob 导出 ${formatBytes(result.bytes)}。`,
        true,
      );
    }
    setStatus("PCM 已就绪 · 分块试听", true);
  } catch (error) {
    showToast(`导出失败：${error.message}`, true);
    setStatus("导出失败");
  } finally {
    state.busy = false;
    elements.exportButton.disabled = false;
  }
}

elements.browseButton.addEventListener("click", () => elements.fileInput.click());
elements.fileInput.addEventListener("change", () => {
  const [file] = elements.fileInput.files;
  void loadFile(file);
  elements.fileInput.value = "";
});

elements.playButton.addEventListener("click", () => void togglePlayback());
elements.rewindButton.addEventListener("click", rewind);
elements.exportButton.addEventListener("click", () => void handleExport());

for (const button of elements.modeButtons) {
  button.addEventListener("click", () => setMode(button.dataset.mode));
}

elements.selStart.addEventListener("input", handleSliderInput);
elements.selEnd.addEventListener("input", handleSliderInput);
elements.blockSize.addEventListener("change", reconfigurePlayback);
elements.prefetchBlocks.addEventListener("change", reconfigurePlayback);
elements.loopToggle.addEventListener("change", () => {
  player.configure({ loop: elements.loopToggle.checked });
});

elements.waveStage.addEventListener("pointerdown", handlePointerDown);
elements.waveStage.addEventListener("pointermove", handlePointerMove);
elements.waveStage.addEventListener("pointerup", handlePointerUp);
elements.waveStage.addEventListener("pointercancel", handlePointerCancel);
elements.waveStage.addEventListener("pointerleave", () => {
  if (!state.drag) {
    state.hoverFrame = null;
    drawOverlay();
  }
});

let dragDepth = 0;
window.addEventListener("dragenter", (event) => {
  if (!event.dataTransfer?.types.includes("Files")) return;
  event.preventDefault();
  dragDepth += 1;
  document.body.classList.add("is-dragging");
});
window.addEventListener("dragover", (event) => {
  if (event.dataTransfer?.types.includes("Files")) event.preventDefault();
});
window.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) document.body.classList.remove("is-dragging");
});
window.addEventListener("drop", (event) => {
  if (!event.dataTransfer?.files.length) return;
  event.preventDefault();
  dragDepth = 0;
  document.body.classList.remove("is-dragging");
  void loadFile(event.dataTransfer.files[0]);
});

window.addEventListener("keydown", (event) => {
  const tag = event.target?.tagName;
  if (["INPUT", "SELECT", "BUTTON", "TEXTAREA"].includes(tag)) return;
  if (!state.format) return;
  if (event.code === "Space") {
    event.preventDefault();
    void togglePlayback();
  } else if (event.key === "Escape") {
    player.pause();
  } else if (event.key === "Home") {
    event.preventDefault();
    void player.seek(0);
  } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    const direction = event.key === "ArrowLeft" ? -1 : 1;
    const amount = state.format.sampleRate * (event.shiftKey ? 10 : 2);
    void player.seek(player.currentFrame + direction * amount);
  }
});

const resizeObserver = new ResizeObserver(() => resizeCanvases());
resizeObserver.observe(elements.waveStage);

setControlsEnabled(false);
updateModeUI();
requestAnimationFrame(resizeCanvases);



