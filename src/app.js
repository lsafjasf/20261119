import {
  amplitudeToDb,
  analyzeBoundary,
  analyzeSpectrum,
  auditTenRounds,
  clamp,
  detectOnsets,
  findNearestZeroCrossing,
  measureLoop,
  mixToMono,
  prepareLoop,
  spectralDistance
} from "./dsp.js";
import { createEndDrumClip, createPhaseOffsetTone } from "./signals.js";

const $ = (selector) => document.querySelector(selector);
const dom = {
  fileInput: $("#file-input"),
  loadTone: $("#load-tone"),
  loadDrums: $("#load-drums"),
  playButton: $("#play-button"),
  playLabel: $("#play-label"),
  auditButton: $("#audit-button"),
  roundReadout: $("#round-readout"),
  timeReadout: $("#time-readout"),
  volume: $("#volume"),
  trackTitle: $("#track-title"),
  waveCanvas: $("#wave-canvas"),
  waveTooltip: $("#wave-tooltip"),
  spectrumCanvas: $("#spectrum-canvas"),
  spectralDelta: $("#spectral-delta"),
  specInFreq: $("#spec-in-freq"),
  specOutFreq: $("#spec-out-freq"),
  auditBars: $("#audit-bars"),
  auditStatus: $("#audit-status"),
  seamSampleJump: $("#seam-sample-jump"),
  rmsRetention: $("#rms-retention"),
  riskBadge: $("#risk-badge"),
  diagnosisCopy: $("#diagnosis-copy"),
  rawDelta: $("#raw-delta"),
  slopeJump: $("#slope-jump"),
  endTransient: $("#end-transient"),
  beatReadout: $("#beat-readout"),
  loopDuration: $("#loop-duration"),
  startRange: $("#start-range"),
  endRange: $("#end-range"),
  fadeRange: $("#fade-range"),
  startValue: $("#start-value"),
  endValue: $("#end-value"),
  fadeValue: $("#fade-value"),
  renderedLength: $("#rendered-length"),
  fadePercent: $("#fade-percent"),
  zeroCrossButton: $("#zero-cross-button"),
  beatSnapButton: $("#beat-snap-button"),
  exportButton: $("#export-button"),
  beatConfidence: $("#beat-confidence"),
  beatList: $("#beat-list"),
  sampleRateNote: $("#sample-rate-note")
};

const state = {
  source: null,
  mono: null,
  duration: 0,
  cropStart: 0,
  cropEnd: 0,
  fadeMs: 40,
  boundary: null,
  spectrumIn: null,
  spectrumOut: null,
  beats: null,
  beatGrid: [],
  nearestEndOnset: null,
  peaks: null,
  loop: null,
  audit: [],
  context: null,
  masterGain: null,
  player: null,
  playing: false,
  mode: "loop",
  playStartedAt: 0,
  playTimer: null,
  auditCompleted: 0,
  drag: null,
  rebuildTimer: null,
  waveMetrics: null,
  animationFrame: null
};

function formatDuration(seconds, includeMs = true) {
  if (!Number.isFinite(seconds)) return "—";
  const whole = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(whole / 60);
  const remainder = seconds - minutes * 60;
  const secText = remainder.toFixed(includeMs ? 3 : 0).padStart(includeMs ? 6 : 2, "0");
  return `${String(minutes).padStart(2, "0")}:${secText}`;
}

function formatDb(value) {
  if (!Number.isFinite(value)) return "—";
  if (value <= -119) return "−∞ dB";
  return `${value.toFixed(1)} dB`;
}

function formatHz(value) {
  if (!value) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(2)} kHz` : `${value.toFixed(1)} Hz`;
}

function buildPeaks(mono, bins = 2400) {
  const minimums = new Float32Array(bins);
  const maximums = new Float32Array(bins);
  const samplesPerBin = mono.length / bins;
  for (let bin = 0; bin < bins; bin += 1) {
    const from = Math.floor(bin * samplesPerBin);
    const to = Math.min(mono.length, Math.floor((bin + 1) * samplesPerBin) + 1);
    let low = 1;
    let high = -1;
    for (let i = from; i < to; i += 1) {
      low = Math.min(low, mono[i]);
      high = Math.max(high, mono[i]);
    }
    minimums[bin] = low;
    maximums[bin] = high;
  }
  return { minimums, maximums };
}

function buildBeatGrid(beats, duration) {
  if (!beats?.bpm) return [];
  if (beats.onsets.length >= 2) return beats.onsets.map((onset) => onset.time);
  const interval = 60 / beats.bpm;
  const grid = [];
  for (let time = 0; time <= duration + interval * 0.25; time += interval) grid.push(time);
  return grid;
}

function findNearestEndOnset(beats, endTime) {
  if (!beats?.onsets.length) return null;
  let nearest = null;
  for (const onset of beats.onsets) {
    const distance = Math.abs(onset.time - endTime);
    if (!nearest || distance < nearest.distance) nearest = { ...onset, distance };
  }
  return nearest && nearest.distance <= 0.3 ? nearest : null;
}

function ensureAudioContext() {
  if (!state.context) {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) throw new Error("当前浏览器不支持 Web Audio API");
    state.context = new Context();
    state.masterGain = state.context.createGain();
    state.masterGain.gain.value = Number(dom.volume.value);
    state.masterGain.connect(state.context.destination);
  }
  if (state.context.state === "suspended") state.context.resume();
  return state.context;
}

function makeAudioBuffer(channels, sampleRate) {
  const context = ensureAudioContext();
  const buffer = context.createBuffer(channels.length, channels[0].length, sampleRate);
  channels.forEach((channel, index) => buffer.copyToChannel(channel, index));
  return buffer;
}

function setSource(source) {
  stopPlayback();
  state.source = source;
  state.mono = mixToMono(source.channels);
  state.duration = state.mono.length / source.sampleRate;
  state.cropStart = 0;
  state.cropEnd = state.duration;
  state.fadeMs = 40;
  state.beats = detectOnsets(state.mono, source.sampleRate);
  state.beatGrid = buildBeatGrid(state.beats, state.duration);
  state.peaks = buildPeaks(state.mono);
  state.audit = [];
  state.auditCompleted = 0;
  dom.trackTitle.textContent = source.name;
  dom.sampleRateNote.textContent = `${source.sampleRate / 1000} kHz · ${source.channels.length} CH · ${formatDuration(state.duration)}`;
  dom.zeroCrossButton.disabled = false;
  dom.beatSnapButton.disabled = !state.beatGrid.length;
  dom.exportButton.disabled = false;
  dom.startRange.disabled = false;
  dom.endRange.disabled = false;
  dom.fadeRange.disabled = false;
  configureRanges();
  rebuildLoop();
  drawWave();
  drawSpectrum();
}

function configureRanges() {
  const step = state.duration > 120 ? 0.01 : 0.001;
  dom.startRange.min = "0";
  dom.startRange.max = state.duration.toFixed(3);
  dom.startRange.step = String(step);
  dom.endRange.min = "0";
  dom.endRange.max = state.duration.toFixed(3);
  dom.endRange.step = String(step);
  dom.startRange.value = String(state.cropStart);
  dom.endRange.value = String(state.cropEnd);
}

function cropSamples() {
  return {
    start: Math.round(state.cropStart * state.source.sampleRate),
    end: Math.round(state.cropEnd * state.source.sampleRate)
  };
}

function clampCropAndFade() {
  const sampleRate = state.source.sampleRate;
  state.cropStart = clamp(state.cropStart, 0, Math.max(0, state.duration - 0.01));
  state.cropEnd = clamp(state.cropEnd, state.cropStart + 0.01, state.duration);
  const cropMs = (state.cropEnd - state.cropStart) * 1000;
  const maxFade = Math.max(5, Math.min(1500, cropMs / 2));
  state.fadeMs = clamp(state.fadeMs, 5, maxFade);
  dom.fadeRange.max = String(Math.floor(maxFade));
  dom.fadeRange.value = String(Math.round(state.fadeMs));
  const samples = cropSamples();
  if (samples.end - samples.start < Math.round(sampleRate * 0.012)) {
    state.cropEnd = Math.min(state.duration, state.cropStart + 0.012);
  }
}

function rebuildLoop() {
  if (!state.source) return;
  clampCropAndFade();
  const { start, end } = cropSamples();
  const fadeSamples = Math.round((state.fadeMs / 1000) * state.source.sampleRate);
  const prepared = prepareLoop(state.source.channels, start, end, fadeSamples);
  const metrics = measureLoop(prepared.channels, state.source.channels, start, end, state.source.sampleRate);
  state.loop = {
    ...prepared,
    metrics,
    audioBuffer: state.context ? makeAudioBuffer(prepared.channels, state.source.sampleRate) : null
  };
  state.boundary = analyzeBoundary(state.mono, state.source.sampleRate, start, end);
  state.spectrumIn = analyzeSpectrum(state.mono, state.source.sampleRate, start, 2048);
  state.spectrumOut = analyzeSpectrum(
    state.mono,
    state.source.sampleRate,
    Math.max(start, end - 2048),
    2048
  );
  state.nearestEndOnset = findNearestEndOnset(state.beats, state.cropEnd);
  state.audit = auditTenRounds(prepared.channels, state.source.channels, start, end, state.source.sampleRate);
  refreshAnalysisUI();
  refreshAuditBars();
  drawSpectrum();
  drawWave();
}

function scheduleRebuild(restart = true) {
  clearTimeout(state.rebuildTimer);
  state.rebuildTimer = setTimeout(() => {
    rebuildLoop();
    if (restart && state.playing) {
      const mode = state.mode;
      startPlayback(mode === "audit" ? 10 : Infinity);
    }
  }, 90);
}

function loadSignal(kind) {
  const sampleRate = state.source?.sampleRate ?? 48000;
  const source = kind === "drums" ? createEndDrumClip(sampleRate) : createPhaseOffsetTone(sampleRate);
  setSource(source);
}

async function loadFile(file) {
  if (!file) return;
  try {
    dom.trackTitle.textContent = `解码 ${file.name}…`;
    const context = ensureAudioContext();
    const bytes = await file.arrayBuffer();
    const buffer = await context.decodeAudioData(bytes.slice(0));
    const channels = Array.from({ length: buffer.numberOfChannels }, (_, index) =>
      buffer.getChannelData(index).slice()
    );
    setSource({
      name: file.name,
      description: "本地音频文件",
      source: "file",
      bpm: null,
      sampleRate: buffer.sampleRate,
      channels
    });
  } catch (error) {
    dom.trackTitle.textContent = "无法解码音频";
    dom.diagnosisCopy.textContent = `载入失败：${error.message}`;
  }
}

function seamRisk() {
  const boundary = state.boundary;
  if (!boundary) return { score: 0, level: "neutral", label: "NO SIGNAL" };
  const edgeLevel = Math.max(boundary.startRms, boundary.endRms, 0.0001);
  const sampleComponent = clamp(boundary.sampleDelta / edgeLevel, 0, 1);
  const spectrumComponent = state.spectrumIn
    ? spectralDistance(state.spectrumIn, state.spectrumOut)
    : 0;
  const onsetComponent = state.nearestEndOnset
    ? state.nearestEndOnset.strength * clamp(1 - state.nearestEndOnset.distance / 0.3, 0, 1)
    : 0;
  const score = clamp(sampleComponent * 0.52 + spectrumComponent * 0.24 + onsetComponent * 0.9, 0, 1);

  if (score >= 0.45) return { score, level: "high", label: "HIGH / 断裂" };
  if (score >= 0.2) return { score, level: "medium", label: "WATCH / 可疑" };
  return { score, level: "low", label: "LOW / 连续" };
}

function diagnosisText(risk) {
  const boundary = state.boundary;
  if (!boundary) return "载入音频或生成测试片段后开始分析。";
  if (risk.level === "high" && state.nearestEndOnset) {
    return `末尾 ${state.nearestEndOnset.time.toFixed(3)} s 检出瞬态，距 OUT 仅 ${(
      state.nearestEndOnset.distance * 1000
    ).toFixed(0)} ms；鼓点被硬切。当前 ${state.fadeMs.toFixed(0)} ms 局部交叉淡化只覆盖接缝，不衰减整段音频。`;
  }
  if (risk.level === "high") {
    return `首尾样本差 ${boundary.sampleDelta.toFixed(4)}（${formatDb(
      boundary.sampleDeltaDb
    )}），首尾相位不连续。用局部交叉淡化重建循环尾→头过渡。`;
  }
  if (state.nearestEndOnset) {
    return `接缝附近仍有瞬态（${state.nearestEndOnset.time.toFixed(
      3
    )} s），建议移动 OUT 或扩大淡化宽度后重新审计。`;
  }
  return `未发现明显样本级断裂；当前局部淡化 ${state.fadeMs.toFixed(
    0
  )} ms，循环缓冲保持原节目电平。`;
}

function refreshAnalysisUI() {
  const boundary = state.boundary;
  if (!boundary || !state.loop) return;
  const risk = seamRisk();
  const spectralDelta = state.spectrumIn
    ? spectralDistance(state.spectrumIn, state.spectrumOut)
    : 0;
  const loopDuration = state.loop.length / state.source.sampleRate;
  const fadePercent = (state.loop.fadeSamples / state.loop.cropLength) * 100;

  dom.rawDelta.textContent = `${boundary.sampleDelta.toFixed(4)} / ${formatDb(boundary.sampleDeltaDb)}`;
  dom.slopeJump.textContent = boundary.slopeJump.toFixed(4);
  dom.endTransient.textContent = state.nearestEndOnset
    ? `${state.nearestEndOnset.time.toFixed(3)} s · ${(state.nearestEndOnset.distance * 1000).toFixed(0)} ms`
    : "未检出";
  dom.beatReadout.textContent = state.beats?.bpm
    ? `${state.beats.bpm.toFixed(1)} BPM · ${state.beats.onsets.length} 拍点`
    : "无周期瞬态";
  dom.loopDuration.textContent = `${(state.cropEnd - state.cropStart).toFixed(3)} s`;
  dom.spectralDelta.textContent = `Δ ${(spectralDelta * 100).toFixed(0)}%`;
  dom.specInFreq.textContent = formatHz(state.spectrumIn?.dominantFrequency);
  dom.specOutFreq.textContent = formatHz(state.spectrumOut?.dominantFrequency);
  dom.startValue.textContent = `${state.cropStart.toFixed(3)} s`;
  dom.endValue.textContent = `${state.cropEnd.toFixed(3)} s`;
  dom.fadeValue.textContent = `${state.fadeMs.toFixed(0)} ms`;
  dom.renderedLength.textContent = `${loopDuration.toFixed(3)} s`;
  dom.fadePercent.textContent = `${fadePercent.toFixed(1)}%`;
  dom.seamSampleJump.textContent = `${state.loop.metrics.stepExcess.toFixed(5)}`;
  dom.seamSampleJump.title = `接缝步长 ${state.loop.metrics.stepRatio.toFixed(2)}× 邻域步长`;
  dom.rmsRetention.textContent = `${(state.loop.metrics.rmsRetention * 100).toFixed(1)}%`;
  dom.riskBadge.className = `risk-badge ${risk.level}`;
  dom.riskBadge.textContent = risk.label;
  dom.diagnosisCopy.textContent = diagnosisText(risk);

  dom.beatConfidence.textContent = state.beats?.bpm
    ? `${Math.round(state.beats.confidence * 100)}%`
    : "—";
  dom.beatList.replaceChildren();
  const beatTimes = state.beatGrid.length ? state.beatGrid : state.beats?.onsets.map((onset) => onset.time) ?? [];
  if (!beatTimes.length) {
    const empty = document.createElement("span");
    empty.className = "empty-state";
    empty.textContent = "无周期瞬态";
    dom.beatList.append(empty);
  } else {
    beatTimes.slice(0, 24).forEach((time) => {
      const pill = document.createElement("span");
      pill.className = "beat-pill";
      pill.textContent = `${time.toFixed(3)}s`;
      dom.beatList.append(pill);
    });
    if (state.nearestEndOnset) {
      const pill = document.createElement("span");
      pill.className = "beat-pill is-end";
      pill.textContent = `尾部 ${state.nearestEndOnset.time.toFixed(3)}s`;
      dom.beatList.append(pill);
    }
  }
  drawWave();
}

function barHeight(reading) {
  if (!reading) return 4;
  if (reading.stepExcess <= 0.00005) return 5;
  return clamp(12 + (Math.log10(reading.stepExcess * 1000 + 1) / 2.2) * 88, 12, 100);
}

function refreshAuditBars() {
  const children = dom.auditBars.children;
  for (let index = 0; index < 10; index += 1) {
    const bar = children[index];
    if (!bar) continue;
    const reading = state.audit[index];
    const done = index < state.auditCompleted;
    bar.classList.toggle("is-done", done && reading?.stepRatio < 1.6);
    bar.classList.toggle("is-bad", done && reading?.stepRatio >= 1.6);
    bar.style.height = `${done ? barHeight(reading) : 4}px`;
    bar.title = done && reading
      ? `第 ${index + 1} 轮：步长 ${reading.stepRatio.toFixed(2)}×，跳变 ${reading.stepExcess.toFixed(5)}`
      : `第 ${index + 1} 轮待测`;
  }

  if (state.auditCompleted === 10) {
    const worst = Math.max(...state.audit.map((reading) => reading.stepRatio));
    const playable = state.audit.every((reading) => reading.playable);
    const pass = worst < 1.6 && playable;
    dom.auditStatus.className = `metric-chip ${pass ? "good" : "warning"}`;
    dom.auditStatus.textContent = pass ? "10/10 通过" : "需调整";
  } else if (state.playing && state.mode === "audit") {
    dom.auditStatus.className = "metric-chip warning";
    dom.auditStatus.textContent = `${state.auditCompleted}/10 播放中`;
  } else if (state.auditCompleted > 0) {
    dom.auditStatus.className = "metric-chip warning";
    dom.auditStatus.textContent = `${state.auditCompleted}/10 已停止`;
  } else {
    dom.auditStatus.className = "metric-chip warning";
    dom.auditStatus.textContent = "待测";
  }
}

function prepareCanvas(canvas) {
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

function niceTimeStep(duration) {
  const raw = duration / 8;
  const powers = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300];
  return powers.find((value) => value >= raw) ?? 600;
}

function drawHandleLabel(context, x, y, text, align, color) {
  context.font = "9px SFMono-Regular, Consolas, monospace";
  const width = context.measureText(text).width + 10;
  const left = align === "right" ? x - width : x;
  context.fillStyle = "rgba(8, 12, 10, 0.9)";
  context.fillRect(left, y, width, 15);
  context.strokeStyle = color;
  context.globalAlpha = 0.45;
  context.strokeRect(left + 0.5, y + 0.5, width - 1, 14);
  context.globalAlpha = 1;
  context.fillStyle = color;
  context.textAlign = "left";
  context.textBaseline = "middle";
  context.fillText(text, left + 5, y + 8);
}

function drawWave() {
  if (!state.peaks || !state.source) return;
  const { context, width, height } = prepareCanvas(dom.waveCanvas);
  const metrics = { left: 18, right: width - 18, top: 35, bottom: height - 25 };
  metrics.plotWidth = Math.max(1, metrics.right - metrics.left);
  metrics.plotHeight = Math.max(1, metrics.bottom - metrics.top);
  const center = metrics.top + metrics.plotHeight / 2;
  const halfHeight = metrics.plotHeight * 0.44;
  const timeToX = (time) => metrics.left + (time / state.duration) * metrics.plotWidth;
  metrics.xStart = timeToX(state.cropStart);
  metrics.xEnd = timeToX(state.cropEnd);
  const fadeSeconds = state.fadeMs / 1000;
  metrics.fadeLeftX = timeToX(Math.min(state.cropEnd, state.cropStart + fadeSeconds));
  metrics.fadeRightX = timeToX(Math.max(state.cropStart, state.cropEnd - fadeSeconds));
  state.waveMetrics = metrics;

  context.fillStyle = "#101512";
  context.fillRect(0, 0, width, height);
  context.strokeStyle = "rgba(225, 235, 227, 0.055)";
  context.lineWidth = 1;
  const tickStep = niceTimeStep(state.duration);
  context.font = "8px SFMono-Regular, Consolas, monospace";
  context.textAlign = "center";
  context.textBaseline = "top";
  for (let time = 0; time <= state.duration + 0.001; time += tickStep) {
    const x = timeToX(time);
    context.beginPath();
    context.moveTo(Math.round(x) + 0.5, metrics.top);
    context.lineTo(Math.round(x) + 0.5, metrics.bottom);
    context.stroke();
    context.fillStyle = "rgba(196, 205, 197, 0.38)";
    context.fillText(time >= 60 ? formatDuration(time, false) : `${time.toFixed(time < 1 ? 1 : 0)}s`, x, height - 18);
  }

  context.fillStyle = "rgba(200, 241, 105, 0.035)";
  context.fillRect(metrics.xStart, metrics.top, metrics.xEnd - metrics.xStart, metrics.plotHeight);
  context.strokeStyle = "rgba(200, 241, 105, 0.15)";
  context.beginPath();
  context.moveTo(metrics.left, center + 0.5);
  context.lineTo(metrics.right, center + 0.5);
  context.stroke();

  const peaks = state.peaks;
  const waveformGradient = context.createLinearGradient(0, metrics.top, 0, metrics.bottom);
  waveformGradient.addColorStop(0, "#b8e8cc");
  waveformGradient.addColorStop(0.5, "#67d9a8");
  waveformGradient.addColorStop(1, "#b8e8cc");
  context.strokeStyle = waveformGradient;
  context.lineWidth = 1;
  context.beginPath();
  for (let x = Math.floor(metrics.left); x <= Math.ceil(metrics.right); x += 1) {
    const bin = clamp(
      Math.floor(((x - metrics.left) / metrics.plotWidth) * peaks.minimums.length),
      0,
      peaks.minimums.length - 1
    );
    const top = center - peaks.maximums[bin] * halfHeight;
    const bottom = center - peaks.minimums[bin] * halfHeight;
    context.moveTo(x + 0.5, top);
    context.lineTo(x + 0.5, Math.max(bottom, top + 0.6));
  }
  context.stroke();

  context.fillStyle = "rgba(4, 8, 6, 0.61)";
  if (metrics.xStart > metrics.left) {
    context.fillRect(metrics.left, metrics.top, metrics.xStart - metrics.left, metrics.plotHeight);
  }
  if (metrics.xEnd < metrics.right) {
    context.fillRect(metrics.xEnd, metrics.top, metrics.right - metrics.xEnd, metrics.plotHeight);
  }

  if (state.loop?.fadeSamples > 0) {
    context.fillStyle = "rgba(245, 191, 91, 0.13)";
    context.fillRect(metrics.xStart, metrics.top, metrics.fadeLeftX - metrics.xStart, metrics.plotHeight);
    context.fillRect(metrics.fadeRightX, metrics.top, metrics.xEnd - metrics.fadeRightX, metrics.plotHeight);
    context.setLineDash([3, 3]);
    context.strokeStyle = "rgba(245, 191, 91, 0.58)";
    for (const x of [metrics.fadeLeftX, metrics.fadeRightX]) {
      context.beginPath();
      context.moveTo(x + 0.5, metrics.top);
      context.lineTo(x + 0.5, metrics.bottom);
      context.stroke();
    }
    context.setLineDash([]);
    context.fillStyle = "rgba(245, 191, 91, 0.75)";
    context.fillRect(metrics.fadeLeftX - 3, metrics.top - 2, 6, 7);
    context.fillRect(metrics.fadeRightX - 3, metrics.top - 2, 6, 7);
    if (metrics.fadeLeftX - metrics.xStart > 46) {
      drawHandleLabel(
        context,
        metrics.xStart + 7,
        metrics.top + 7,
        `FADE ${state.fadeMs.toFixed(0)}ms`,
        "left",
        "#f5bf5b"
      );
    }
  }

  context.strokeStyle = "rgba(245, 191, 91, 0.24)";
  context.lineWidth = 1;
  for (let index = 0; index < state.beatGrid.length; index += 1) {
    const x = timeToX(state.beatGrid[index]);
    if (x < metrics.left || x > metrics.right) continue;
    context.globalAlpha = index % 4 === 0 ? 0.52 : 0.22;
    context.beginPath();
    context.moveTo(Math.round(x) + 0.5, metrics.top);
    context.lineTo(Math.round(x) + 0.5, metrics.bottom);
    context.stroke();
  }
  context.globalAlpha = 1;

  if (state.nearestEndOnset) {
    const x = timeToX(state.nearestEndOnset.time);
    context.strokeStyle = "rgba(255, 100, 100, 0.86)";
    context.beginPath();
    context.moveTo(Math.round(x) + 0.5, metrics.top);
    context.lineTo(Math.round(x) + 0.5, metrics.bottom);
    context.stroke();
    context.fillStyle = "#ff6464";
    context.beginPath();
    context.moveTo(x - 5, metrics.top + 4);
    context.lineTo(x + 5, metrics.top + 4);
    context.lineTo(x, metrics.top + 10);
    context.closePath();
    context.fill();
  }

  for (const [x, label, align] of [
    [metrics.xStart, `IN ${state.cropStart.toFixed(3)}s`, "left"],
    [metrics.xEnd, `OUT ${state.cropEnd.toFixed(3)}s`, "right"]
  ]) {
    context.strokeStyle = "#c8f169";
    context.lineWidth = 1.5;
    context.beginPath();
    context.moveTo(Math.round(x) + 0.5, metrics.top - 8);
    context.lineTo(Math.round(x) + 0.5, metrics.bottom);
    context.stroke();
    context.fillStyle = "#c8f169";
    context.fillRect(x - 4, metrics.top - 10, 8, 8);
    drawHandleLabel(context, x + (align === "left" ? 7 : -7), 5, label, align, "#c8f169");
  }

  if (state.playing && state.context) {
    const elapsed = state.context.currentTime - state.playStartedAt;
    const loopDuration = state.loop.length / state.source.sampleRate;
    const progress = loopDuration > 0 ? (elapsed % loopDuration) / loopDuration : 0;
    const x = metrics.xStart + progress * (metrics.xEnd - metrics.xStart);
    context.shadowColor = "rgba(255, 255, 255, 0.8)";
    context.shadowBlur = 6;
    context.strokeStyle = "rgba(255, 255, 245, 0.92)";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(Math.round(x) + 0.5, metrics.top);
    context.lineTo(Math.round(x) + 0.5, metrics.bottom);
    context.stroke();
    context.shadowBlur = 0;
  }
}

function spectrumPath(context, spectrum, maxMagnitude, width, height, color) {
  const left = 0;
  const right = width;
  const top = 4;
  const bottom = height - 5;
  const minFrequency = 20;
  const maxFrequency = 20000;
  const points = [];
  for (let index = 1; index < spectrum.magnitudes.length; index += 1) {
    const frequency = spectrum.frequencies[index];
    if (frequency < minFrequency || frequency > maxFrequency) continue;
    const x = left + (Math.log(frequency / minFrequency) / Math.log(maxFrequency / minFrequency)) * (right - left);
    const db = amplitudeToDb(spectrum.magnitudes[index] / Math.max(maxMagnitude, 1e-9));
    const y = top + clamp(-db / 58, 0, 1) * (bottom - top);
    points.push([x, y]);
  }
  if (!points.length) return;

  context.beginPath();
  context.moveTo(points[0][0], bottom);
  for (const [x, y] of points) context.lineTo(x, y);
  context.lineTo(points[points.length - 1][0], bottom);
  context.closePath();
  context.fillStyle = `${color}12`;
  context.fill();

  context.beginPath();
  points.forEach(([x, y], index) => {
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.strokeStyle = color;
  context.lineWidth = 1.25;
  context.stroke();
}

function drawSpectrum() {
  if (!state.spectrumIn || !state.spectrumOut) return;
  const { context, width, height } = prepareCanvas(dom.spectrumCanvas);
  context.fillStyle = "rgba(8, 12, 10, 0.42)";
  context.fillRect(0, 0, width, height);
  const left = 0;
  const right = width;
  const top = 4;
  const bottom = height - 5;
  const minFrequency = 20;
  const maxFrequency = 20000;

  context.strokeStyle = "rgba(225, 235, 227, 0.08)";
  context.font = "7px SFMono-Regular, Consolas, monospace";
  context.textAlign = "center";
  context.fillStyle = "rgba(190, 200, 192, 0.35)";
  for (const frequency of [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]) {
    const x = left + (Math.log(frequency / minFrequency) / Math.log(maxFrequency / minFrequency)) * (right - left);
    context.beginPath();
    context.moveTo(Math.round(x) + 0.5, top);
    context.lineTo(Math.round(x) + 0.5, bottom);
    context.stroke();
    const label = frequency >= 1000 ? `${frequency / 1000}k` : String(frequency);
    context.fillText(label, x, height - 1);
  }
  for (const fraction of [0.25, 0.5, 0.75]) {
    const y = top + fraction * (bottom - top);
    context.beginPath();
    context.moveTo(left, Math.round(y) + 0.5);
    context.lineTo(right, Math.round(y) + 0.5);
    context.stroke();
  }

  const maxMagnitude = Math.max(
    Math.max(...state.spectrumIn.magnitudes),
    Math.max(...state.spectrumOut.magnitudes),
    1e-9
  );
  spectrumPath(context, state.spectrumOut, maxMagnitude, width, height, "#ff8b6a");
  spectrumPath(context, state.spectrumIn, maxMagnitude, width, height, "#67d9d2");
}

function stopPlayback(silent = false) {
  if (state.player) {
    state.player.onended = null;
    try {
      state.player.stop();
    } catch {
    }
    state.player.disconnect();
    state.player = null;
  }
  if (state.animationFrame) cancelAnimationFrame(state.animationFrame);
  state.animationFrame = null;
  state.playing = false;
  dom.playButton.classList.remove("is-playing");
  dom.playButton.setAttribute("aria-pressed", "false");
  dom.playLabel.textContent = "试听无缝循环";
  dom.playButton.querySelector(".play-icon").textContent = "▶";
  if (!silent) {
    dom.timeReadout.textContent = "00:00.000";
    dom.roundReadout.textContent = state.mode === "audit" ? `ROUND ${state.auditCompleted} / 10` : "ROUND — / 10";
  }
  refreshAuditBars();
  drawWave();
}

function startPlayback(maxRounds = Infinity) {
  if (!state.loop) return;
  stopPlayback(true);
  const context = ensureAudioContext();
  if (!state.loop.audioBuffer) {
    state.loop.audioBuffer = makeAudioBuffer(state.loop.channels, state.source.sampleRate);
  }
  state.mode = Number.isFinite(maxRounds) ? "audit" : "loop";
  state.auditCompleted = 0;
  state.playing = true;
  state.playStartedAt = context.currentTime;
  const player = context.createBufferSource();
  player.buffer = state.loop.audioBuffer;
  player.loop = true;
  player.loopStart = 0;
  player.loopEnd = state.loop.audioBuffer.duration;
  player.connect(state.masterGain);
  player.start();
  if (Number.isFinite(maxRounds)) {
    player.stop(context.currentTime + state.loop.audioBuffer.duration * maxRounds);
  }
  state.player = player;
  dom.playButton.classList.add("is-playing");
  dom.playButton.setAttribute("aria-pressed", "true");
  dom.playLabel.textContent = state.mode === "audit" ? "停止十轮审计" : "停止试听";
  dom.playButton.querySelector(".play-icon").textContent = "■";
  refreshAuditBars();
  updatePlayback();
}

function updatePlayback() {
  if (!state.playing || !state.context || !state.loop) return;
  const context = state.context;
  const loopDuration = state.loop.length / state.source.sampleRate;
  const elapsed = Math.max(0, context.currentTime - state.playStartedAt);
  const position = elapsed % loopDuration;
  dom.timeReadout.textContent = formatDuration(position);

  if (state.mode === "audit") {
    const completed = clamp(Math.floor(elapsed / loopDuration), 0, 10);
    if (completed > state.auditCompleted) {
      state.auditCompleted = completed;
      refreshAuditBars();
    }
    dom.roundReadout.textContent = `ROUND ${state.auditCompleted} / 10`;
    if (elapsed >= loopDuration * 10 - 0.0005) {
      state.auditCompleted = 10;
      stopPlayback(true);
      refreshAuditBars();
      dom.roundReadout.textContent = "ROUND 10 / 10";
      dom.timeReadout.textContent = formatDuration(loopDuration);
      return;
    }
  } else {
    const round = Math.floor(elapsed / loopDuration) + 1;
    dom.roundReadout.textContent = `LOOP ${round} · ${loopDuration.toFixed(2)}s`;
  }

  drawWave();
  state.animationFrame = requestAnimationFrame(updatePlayback);
}

function encodeWav(channels, sampleRate) {
  const channelCount = channels.length;
  const frameCount = channels[0].length;
  const bytesPerSample = 2;
  const dataSize = frameCount * channelCount * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);
  let offset = 0;

  const writeString = (text) => {
    for (let index = 0; index < text.length; index += 1) view.setUint8(offset++, text.charCodeAt(index));
  };
  writeString("RIFF");
  view.setUint32(offset, 36 + dataSize, true);
  offset += 4;
  writeString("WAVE");
  writeString("fmt ");
  view.setUint32(offset, 16, true);
  offset += 4;
  view.setUint16(offset, 1, true);
  offset += 2;
  view.setUint16(offset, channelCount, true);
  offset += 2;
  view.setUint32(offset, sampleRate, true);
  offset += 4;
  view.setUint32(offset, sampleRate * channelCount * bytesPerSample, true);
  offset += 4;
  view.setUint16(offset, channelCount * bytesPerSample, true);
  offset += 2;
  view.setUint16(offset, bytesPerSample * 8, true);
  offset += 2;
  writeString("data");
  view.setUint32(offset, dataSize, true);
  offset += 4;

  for (let frame = 0; frame < frameCount; frame += 1) {
    for (let channel = 0; channel < channelCount; channel += 1) {
      const sample = clamp(channels[channel][frame], -1, 1);
      view.setInt16(offset, Math.round(sample * 32767), true);
      offset += 2;
    }
  }
  return new Blob([buffer], { type: "audio/wav" });
}

function exportLoop() {
  if (!state.loop) return;
  const blob = encodeWav(state.loop.channels, state.source.sampleRate);
  const base = state.source.name.replace(/[\\/:*?"<>|]+/g, "-").replace(/\.[^.]+$/, "");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${base}-loop-${state.fadeMs.toFixed(0)}ms.wav`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

function syncGeometryControls() {
  dom.startRange.value = String(state.cropStart);
  dom.endRange.value = String(state.cropEnd);
  dom.fadeRange.value = String(Math.round(state.fadeMs));
  dom.startValue.textContent = `${state.cropStart.toFixed(3)} s`;
  dom.endValue.textContent = `${state.cropEnd.toFixed(3)} s`;
  dom.fadeValue.textContent = `${state.fadeMs.toFixed(0)} ms`;
  dom.loopDuration.textContent = `${(state.cropEnd - state.cropStart).toFixed(3)} s`;
  const cropMs = Math.max(1, (state.cropEnd - state.cropStart) * 1000);
  dom.fadePercent.textContent = `${((state.fadeMs / cropMs) * 100).toFixed(1)}%`;
  drawWave();
}

function applyCropChange(restart = true) {
  if (!state.source) return;
  clampCropAndFade();
  syncGeometryControls();
  scheduleRebuild(restart);
}

function nearestBeat(time) {
  if (!state.beatGrid.length) return time;
  return state.beatGrid.reduce((nearest, candidate) =>
    Math.abs(candidate - time) < Math.abs(nearest - time) ? candidate : nearest
  , state.beatGrid[0]);
}

function handleWavePointerDown(event) {
  if (!state.source || !state.waveMetrics) return;
  const rect = dom.waveCanvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const metrics = state.waveMetrics;
  let drag = null;

  if (Math.abs(x - metrics.xStart) <= 8) drag = "start";
  else if (Math.abs(x - metrics.xEnd) <= 8) drag = "end";
  else if (Math.abs(x - metrics.fadeLeftX) <= 7) drag = "fade-left";
  else if (Math.abs(x - metrics.fadeRightX) <= 7) drag = "fade-right";
  if (!drag && y > metrics.top && y < metrics.bottom) {
    if (Math.abs(x - metrics.xStart) <= 14) drag = "start";
    else if (Math.abs(x - metrics.xEnd) <= 14) drag = "end";
  }
  if (!drag) return;

  state.drag = drag;
  dom.waveCanvas.setPointerCapture(event.pointerId);
  event.preventDefault();
  updateWavePointer(event);
}

function updateWavePointer(event) {
  if (!state.waveMetrics || !state.source) return;
  const rect = dom.waveCanvas.getBoundingClientRect();
  const x = clamp(event.clientX - rect.left, state.waveMetrics.left, state.waveMetrics.right);
  const y = clamp(event.clientY - rect.top, 0, rect.height);
  const time = ((x - state.waveMetrics.left) / state.waveMetrics.plotWidth) * state.duration;
  const sampleIndex = clamp(Math.round(time * state.source.sampleRate), 0, state.mono.length - 1);
  const amplitude = state.mono[sampleIndex] ?? 0;

  dom.waveTooltip.hidden = false;
  dom.waveTooltip.textContent = `${time.toFixed(3)} s · ${amplitude >= 0 ? "+" : ""}${amplitude.toFixed(3)}`;
  dom.waveTooltip.style.left = `${clamp(x + 10, 5, rect.width - 120)}px`;
  dom.waveTooltip.style.top = `${clamp(y - 28, 5, rect.height - 30)}px`;

  if (!state.drag) {
    const metrics = state.waveMetrics;
    const nearHandle =
      Math.abs(x - metrics.xStart) <= 8 ||
      Math.abs(x - metrics.xEnd) <= 8 ||
      Math.abs(x - metrics.fadeLeftX) <= 7 ||
      Math.abs(x - metrics.fadeRightX) <= 7;
    dom.waveCanvas.style.cursor = nearHandle ? "col-resize" : "crosshair";
    return;
  }

  if (state.drag === "start") {
    state.cropStart = clamp(time, 0, state.cropEnd - 0.01);
  } else if (state.drag === "end") {
    state.cropEnd = clamp(time, state.cropStart + 0.01, state.duration);
  } else {
    const cropMs = (state.cropEnd - state.cropStart) * 1000;
    const maxFade = Math.max(5, Math.min(1500, cropMs / 2));
    const fromStart = (time - state.cropStart) * 1000;
    const fromEnd = (state.cropEnd - time) * 1000;
    state.fadeMs = clamp(state.drag === "fade-left" ? fromStart : fromEnd, 5, maxFade);
  }
  clampCropAndFade();
  syncGeometryControls();
  scheduleRebuild(true);
}

function handleWavePointerUp(event) {
  if (!state.drag) return;
  state.drag = null;
  if (dom.waveCanvas.hasPointerCapture(event.pointerId)) {
    dom.waveCanvas.releasePointerCapture(event.pointerId);
  }
  scheduleRebuild(true);
}

function handleWavePointerLeave() {
  if (state.drag) return;
  dom.waveTooltip.hidden = true;
  dom.waveCanvas.style.cursor = "crosshair";
}

function snapToZeroCrossings() {
  if (!state.source) return;
  const sampleRate = state.source.sampleRate;
  const startSample = Math.round(state.cropStart * sampleRate);
  const endSample = Math.round(state.cropEnd * sampleRate);
  const radius = Math.round(sampleRate * 0.04);
  const start = findNearestZeroCrossing(state.mono, startSample, radius);
  const end = findNearestZeroCrossing(state.mono, endSample, radius);
  state.cropStart = start / sampleRate;
  state.cropEnd = Math.max(state.cropStart + 0.01, end / sampleRate);
  clampCropAndFade();
  syncGeometryControls();
  rebuildLoop();
  if (state.playing) startPlayback(state.mode === "audit" ? 10 : Infinity);
}

function snapToBeats() {
  if (!state.beatGrid.length) return;
  state.cropStart = nearestBeat(state.cropStart);
  state.cropEnd = Math.max(state.cropStart + 0.01, nearestBeat(state.cropEnd));
  clampCropAndFade();
  syncGeometryControls();
  rebuildLoop();
  if (state.playing) startPlayback(state.mode === "audit" ? 10 : Infinity);
}

function bindEvents() {
  dom.loadTone.addEventListener("click", () => loadSignal("tone"));
  dom.loadDrums.addEventListener("click", () => loadSignal("drums"));
  dom.fileInput.addEventListener("change", () => loadFile(dom.fileInput.files?.[0]));
  dom.playButton.addEventListener("click", () => {
    if (state.playing) stopPlayback();
    else startPlayback();
  });
  dom.auditButton.addEventListener("click", () => {
    if (state.playing && state.mode === "audit") stopPlayback();
    else startPlayback(10);
  });
  dom.volume.addEventListener("input", () => {
    if (state.masterGain && state.context) {
      state.masterGain.gain.setTargetAtTime(Number(dom.volume.value), state.context.currentTime, 0.015);
    }
  });
  dom.startRange.addEventListener("input", () => {
    state.cropStart = Number(dom.startRange.value);
    applyCropChange();
  });
  dom.endRange.addEventListener("input", () => {
    state.cropEnd = Number(dom.endRange.value);
    applyCropChange();
  });
  dom.fadeRange.addEventListener("input", () => {
    state.fadeMs = Number(dom.fadeRange.value);
    applyCropChange();
  });
  dom.zeroCrossButton.addEventListener("click", snapToZeroCrossings);
  dom.beatSnapButton.addEventListener("click", snapToBeats);
  dom.exportButton.addEventListener("click", exportLoop);
  dom.waveCanvas.addEventListener("pointerdown", handleWavePointerDown);
  dom.waveCanvas.addEventListener("pointermove", updateWavePointer);
  dom.waveCanvas.addEventListener("pointerup", handleWavePointerUp);
  dom.waveCanvas.addEventListener("pointercancel", handleWavePointerUp);
  dom.waveCanvas.addEventListener("pointerleave", handleWavePointerLeave);
  window.addEventListener("resize", () => {
    drawWave();
    drawSpectrum();
  });
  document.addEventListener("dragover", (event) => event.preventDefault());
  document.addEventListener("drop", (event) => {
    event.preventDefault();
    loadFile(event.dataTransfer?.files?.[0]);
  });
}

function init() {
  for (let index = 0; index < 10; index += 1) {
    const bar = document.createElement("div");
    bar.className = "audit-bar";
    bar.dataset.round = String(index + 1);
    bar.style.height = "4px";
    dom.auditBars.append(bar);
  }
  bindEvents();
  loadSignal("tone");
}

init();
