import {
  DEG,
  angularDistance,
  audioRms,
  beamformAtAngle,
  diagnoseChannelMap,
  encodeWav,
  findPeaks,
  makeCircularArray,
  mixDown,
  normalizeDegrees,
  scanBeam,
  swapChannels,
  synthesizeArray
} from './dsp.js';

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const state = {
  sourceMode: 'single',
  sourceAngles: [37, 138],
  reverbEnabled: true,
  reverbAmount: 0.38,
  micCount: 8,
  radius: 0.12,
  soundSpeed: 343,
  sampleRate: 16000,
  mappingMode: 'identity',
  baseArray: [],
  baseChannels: [],
  coords: [],
  channels: [],
  scan: null,
  peaks: [],
  diagnosis: null,
  selectedAngle: null,
  enhanced: null,
  rawMix: null,
  urls: { raw: null, enhanced: null }
};

const refs = {
  runButton: $('#runButton'),
  runStatus: $('#runStatus'),
  runTime: $('#runTime'),
  estimateAngle: $('#estimateAngle'),
  estimateFoot: $('#estimateFoot'),
  angleError: $('#angleError'),
  angleErrorFoot: $('#angleErrorFoot'),
  errorMode: $('#errorMode'),
  confidenceValue: $('#confidenceValue'),
  confidenceMeter: $('#confidenceMeter'),
  confidenceFoot: $('#confidenceFoot'),
  contrastValue: $('#contrastValue'),
  contrastFoot: $('#contrastFoot'),
  peakList: $('#peakList'),
  peakCount: $('#peakCount'),
  mappingStatus: $('#mappingStatus'),
  mappingMessage: $('#mappingMessage'),
  mappingResidual: $('#mappingResidual'),
  mappingRecommendation: $('#mappingRecommendation'),
  coordinateCount: $('#coordinateCount'),
  coordinateList: $('#coordinateList'),
  symmetryChip: $('#symmetryChip'),
  symmetryLead: $('#symmetryLead'),
  symmetryNote: $('#symmetryNote'),
  rotationPeriod: $('#rotationPeriod'),
  mirrorAxes: $('#mirrorAxes'),
  globalAmbiguity: $('#globalAmbiguity'),
  steerAngle: $('#steerAngle'),
  rawAudio: $('#rawAudio'),
  enhancedAudio: $('#enhancedAudio'),
  rawDownload: $('#rawDownload'),
  enhancedDownload: $('#enhancedDownload'),
  rawRms: $('#rawRms'),
  enhancedRms: $('#enhancedRms'),
  toast: $('#toast'),
  polarCanvas: $('#polarCanvas'),
  arrayCanvas: $('#arrayCanvas'),
  rawWaveCanvas: $('#rawWaveCanvas'),
  enhancedWaveCanvas: $('#enhancedWaveCanvas')
};

let runCounter = 0;
let toastTimer = null;
let resizeTimer = null;

function nextPaint() {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  });
}

function setStatus(text, busy = false) {
  refs.runStatus.textContent = text;
  refs.runButton.disabled = busy;
}

function showToast(message, error = false) {
  clearTimeout(toastTimer);
  refs.toast.textContent = message;
  refs.toast.classList.toggle('is-error', error);
  refs.toast.classList.add('is-visible');
  toastTimer = setTimeout(() => refs.toast.classList.remove('is-visible'), 2600);
}

function formatAngle(value) {
  return `${normalizeDegrees(value).toFixed(1)}°`;
}

function sourceList() {
  const count = state.sourceMode === 'dual' ? 2 : 1;
  return state.sourceAngles.slice(0, count).map((angleDeg, index) => ({
    angleDeg,
    level: index === 0 ? 0.72 : 0.56,
    seed: index === 0 ? 101 : 888,
    baseFrequency: index === 0 ? 590 : 860
  }));
}

function mappedInputs() {
  if (state.mappingMode === 'identity') {
    return {
      channels: state.baseChannels.slice(),
      coords: state.baseArray.map((mic) => ({ ...mic }))
    };
  }
  const swappedChannels = swapChannels(state.baseChannels, 0, 1);
  if (state.mappingMode === 'remapped') {
    return {
      channels: swappedChannels,
      coords: swapChannels(state.baseArray, 0, 1).map((mic) => ({ ...mic }))
    };
  }
  return {
    channels: swappedChannels,
    coords: state.baseArray.map((mic) => ({ ...mic }))
  };
}

function setAudioUrl(kind, buffer) {
  if (state.urls[kind]) URL.revokeObjectURL(state.urls[kind]);
  const url = URL.createObjectURL(new Blob([buffer], { type: 'audio/wav' }));
  state.urls[kind] = url;
  return url;
}

function analyzeCurrent({ preserveSelection = false } = {}) {
  const mapped = mappedInputs();
  state.channels = mapped.channels;
  state.coords = mapped.coords;

  const started = performance.now();
  state.scan = scanBeam({
    channels: state.channels,
    array: state.coords,
    speedOfSound: state.soundSpeed,
    sampleRate: state.sampleRate,
    stepDeg: 2
  });
  state.peaks = findPeaks(state.scan, { maxPeaks: 16, minSeparationDeg: 8 });

  const topPeak = state.peaks[0];
  if (!preserveSelection || state.selectedAngle === null) {
    state.selectedAngle = topPeak?.angle ?? 0;
  }

  state.diagnosis = diagnoseChannelMap({
    channels: state.channels,
    array: state.coords,
    speedOfSound: state.soundSpeed,
    sampleRate: state.sampleRate,
    referenceAngle: topPeak?.angle ?? 0,
    candidateAngles: state.peaks.slice(0, 2).map((peak) => peak.angle)
  });

  state.rawMix = mixDown(state.channels);
  state.enhanced = beamformAtAngle({
    channels: state.channels,
    array: state.coords,
    angleDeg: state.selectedAngle,
    speedOfSound: state.soundSpeed,
    sampleRate: state.sampleRate
  });

  const elapsed = performance.now() - started;
  updateMetrics(elapsed);
  updatePeakList();
  updateMappingPanel();
  updateSymmetryPanel();
  updateAudio();
  drawAll();
}

async function runSimulation({ preserveSelection = false, announce = false } = {}) {
  const token = ++runCounter;
  setStatus('合成多通道信号 · 扫描中', true);
  await nextPaint();
  if (token !== runCounter) return;

  try {
    state.baseArray = makeCircularArray(state.micCount, state.radius);
    const simulation = synthesizeArray({
      array: state.baseArray,
      sources: sourceList(),
      speedOfSound: state.soundSpeed,
      sampleRate: state.sampleRate,
      duration: 2.4,
      reverb: state.reverbEnabled ? state.reverbAmount : 0,
      ambient: state.reverbEnabled ? 0.024 : 0.012
    });
    state.baseChannels = simulation.channels;
    analyzeCurrent({ preserveSelection });
    setStatus(`${state.sourceMode === 'dual' ? '双源' : '单源'}扫描完成 · 映射${state.mappingMode === 'identity' ? '一致' : '已调整'}`);
    if (announce) showToast('已重新合成多通道信号并完成波束扫描');
  } catch (error) {
    console.error(error);
    setStatus('DSP 运行失败');
    showToast(`运行失败：${error.message}`, true);
  } finally {
    if (token === runCounter) refs.runButton.disabled = false;
  }
}

function applyMapping(mode, message) {
  if (!state.baseChannels.length) return;
  state.mappingMode = mode;
  analyzeCurrent({ preserveSelection: false });
  setStatus(`映射${mode === 'identity' ? '已恢复' : mode === 'remapped' ? '已同步重映射' : '未重映射'}`);
  showToast(message, mode === 'unmapped');
}

function setSourceMode(mode) {
  if (state.sourceMode === mode) return;
  state.sourceMode = mode;
  $$('.segment').forEach((button) => {
    button.classList.toggle('is-active', button.dataset.sourceMode === mode);
  });
  $('#sourceBControl').classList.toggle('is-hidden', mode !== 'dual');
  runSimulation({ preserveSelection: false });
}

function updateRangeLabels() {
  $('#sourceAAngleValue').textContent = `${state.sourceAngles[0]}°`;
  $('#sourceBAngleValue').textContent = `${state.sourceAngles[1]}°`;
  $('#reverbAmountValue').textContent = `${Math.round(state.reverbAmount * 100)}%`;
  $('#apertureLabel').textContent = `Ø ${Math.round(state.radius * 200)} cm`;
}

function syncSettingsFromControls() {
  state.sourceAngles[0] = Number($('#sourceAAngle').value);
  state.sourceAngles[1] = Number($('#sourceBAngle').value);
  state.reverbEnabled = $('#reverbEnabled').checked;
  state.reverbAmount = Number($('#reverbAmount').value) / 100;
  state.micCount = Number($('#micCount').value);
  state.radius = Number($('#arrayRadius').value) / 100;
  state.soundSpeed = Number($('#soundSpeed').value);
  clampSettings();
  $('#footerMicCount').textContent = state.micCount;
  $('#footerSoundSpeed').textContent = state.soundSpeed;
  updateRangeLabels();
}

function nearestPeakTo(angle) {
  if (!state.peaks.length) return null;
  return state.peaks.reduce((best, peak) => (
    !best || angularDistance(peak.angle, angle) < angularDistance(best.angle, angle) ? peak : best
  ), null);
}

function updateMetrics(elapsed) {
  const top = state.peaks[0];
  const sources = sourceList();
  refs.runTime.textContent = `${elapsed.toFixed(0)} ms`;

  if (!top) {
    refs.estimateAngle.textContent = '--';
    refs.angleError.textContent = '--';
    refs.confidenceValue.textContent = '--';
    refs.contrastValue.textContent = '--';
    refs.estimateFoot.textContent = '未检测到有效峰值';
    return;
  }

  const sourceErrors = sources.map((source) => {
    const peak = nearestPeakTo(source.angleDeg);
    return {
      angleDeg: source.angleDeg,
      peak,
      error: peak ? angularDistance(peak.angle, source.angleDeg) : null
    };
  });
  const directError = Math.min(...sourceErrors.map((item) => item.error ?? Infinity));

  refs.estimateAngle.textContent = top.angle.toFixed(1);
  refs.estimateFoot.textContent = `峰值 #1 · 相对主峰 ${top.relativeDb.toFixed(1)} dB · ${state.reverbEnabled ? `混响 ${Math.round(state.reverbAmount * 100)}%` : '无混响'}`;
  refs.angleError.textContent = Number.isFinite(directError) ? directError.toFixed(1) : '--';
  refs.errorMode.textContent = state.sourceMode === 'dual' ? '2-SOURCE' : 'DIRECT';
  refs.angleErrorFoot.textContent = sourceErrors.map((item, index) => (
    `${index === 0 ? 'A' : 'B'} ${item.angleDeg.toFixed(0)}° → ${item.peak ? `${item.peak.angle.toFixed(1)}°` : '未检出'}`
  )).join(' · ');

  const confidence = Math.round(top.confidence * 100);
  refs.confidenceValue.textContent = confidence;
  refs.confidenceMeter.style.width = `${confidence}%`;
  refs.confidenceFoot.textContent = `峰值相干度 ${(top.coherence * 100).toFixed(0)}% · 共 ${state.peaks.length} 个局部峰`;
  refs.contrastValue.textContent = top.contrastDb.toFixed(1);
  refs.contrastFoot.textContent = state.peaks[1]
    ? `次峰 ${state.peaks[1].angle.toFixed(1)}° · ${state.peaks[1].relativeDb.toFixed(1)} dB`
    : '扫描背景中位数以上的主瓣';

  if (state.mappingMode === 'unmapped') {
    refs.estimateFoot.textContent = '映射异常 · 方位读数仅用于诊断对照';
  }
}

function updatePeakList() {
  refs.peakList.replaceChildren();
  refs.peakCount.textContent = `${state.peaks.length} PEAKS`;
  if (!state.peaks.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    empty.textContent = '运行扫描后显示峰值';
    refs.peakList.append(empty);
    return;
  }

  state.peaks.slice(0, 6).forEach((peak, index) => {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'peak-row';
    row.title = `峰值 ${index + 1}：${peak.angle.toFixed(1)}°，相对主峰 ${peak.relativeDb.toFixed(1)} dB，置信度 ${(peak.confidence * 100).toFixed(0)}%`;
    row.classList.toggle('is-selected', angularDistance(peak.angle, state.selectedAngle) < 1.1);
    row.addEventListener('click', () => {
      state.selectedAngle = peak.angle;
      updateAudio();
      updatePeakList();
      drawAll();
    });

    const rank = document.createElement('span');
    rank.className = 'peak-rank';
    rank.textContent = String(index + 1).padStart(2, '0');

    const angle = document.createElement('span');
    angle.className = 'peak-angle';
    angle.append(document.createTextNode(peak.angle.toFixed(1)));
    const degree = document.createElement('small');
    degree.textContent = '°';
    angle.append(degree);

    const bar = document.createElement('span');
    bar.className = 'peak-bar';
    const fill = document.createElement('span');
    fill.style.width = `${Math.max(2, peak.confidence * 100)}%`;
    bar.append(fill);

    const confidence = document.createElement('span');
    confidence.className = 'peak-confidence';
    confidence.textContent = `${Math.round(peak.confidence * 100)}%`;

    row.append(rank, angle, bar, confidence);
    refs.peakList.append(row);
  });
}

function updateMappingPanel() {
  const diagnosis = state.diagnosis;
  if (!diagnosis) return;
  const knownIssue = state.mappingMode === 'unmapped';
  const detectedIssue = Boolean(diagnosis.suspectSwap)
    && (knownIssue || diagnosis.improvement > 0.2);
  const hasIssue = detectedIssue || knownIssue;
  const swap = diagnosis.suspectSwap;
  const displayedSwap = knownIssue ? [0, 1] : swap;

  refs.mappingStatus.classList.remove('is-neutral', 'is-ok', 'is-warn');
  if (hasIssue) {
    refs.mappingStatus.classList.add('is-warn');
    refs.mappingStatus.textContent = '检测到映射异常';
  } else if (state.mappingMode === 'remapped') {
    refs.mappingStatus.classList.add('is-ok');
    refs.mappingStatus.textContent = '已显式重映射';
  } else {
    refs.mappingStatus.classList.add('is-ok');
    refs.mappingStatus.textContent = '映射一致';
  }

  refs.mappingResidual.textContent = `${diagnosis.bestResidual.toFixed(2)} smp`;
  if (detectedIssue && displayedSwap) {
    refs.mappingRecommendation.textContent = `CH${displayedSwap[0] + 1} ↔ CH${displayedSwap[1] + 1}`;
  } else if (knownIssue) {
    refs.mappingRecommendation.textContent = 'CH1 ↔ CH2';
  } else if (state.mappingMode === 'remapped') {
    refs.mappingRecommendation.textContent = '已同步';
  } else {
    refs.mappingRecommendation.textContent = 'identity';
  }

  if (detectedIssue && displayedSwap) {
    refs.mappingMessage.textContent = `通道间 TDOA 与当前坐标不一致，拟合最支持交换 CH${displayedSwap[0] + 1} 与 CH${displayedSwap[1] + 1}。当前方位读数因此不可作为最终定位结果。`;
  } else if (knownIssue) {
    refs.mappingMessage.textContent = '数据通道 CH1/CH2 已交换，但坐标清单仍保持原顺序。请执行“交换并重映射坐标”，否则扫描会使用错误的阵元位置。';
  } else if (state.mappingMode === 'remapped') {
    refs.mappingMessage.textContent = '数据顺序与坐标清单已同步交换，通道映射保持一致。整周扫描结果与原始标定等价。';
  } else {
    refs.mappingMessage.textContent = 'TDOA 拟合支持当前 CH→坐标 映射，未发现通道顺序错位。可继续交换通道进行映射体检。';
  }

  $('#swapDataButton').classList.toggle('is-active', state.mappingMode === 'unmapped');
  $('#remapButton').classList.toggle('is-active', state.mappingMode === 'remapped');

  refs.coordinateCount.textContent = `${state.coords.length} CH`;
  refs.coordinateList.replaceChildren();
  state.coords.forEach((mic, index) => {
    const item = document.createElement('div');
    item.className = 'coordinate-item';
    const label = document.createElement('span');
    label.textContent = `CH${index + 1}`;
    const value = document.createElement('span');
    value.textContent = `${mic.x >= 0 ? '+' : ''}${mic.x.toFixed(3)}, ${mic.y >= 0 ? '+' : ''}${mic.y.toFixed(3)}`;
    item.append(label, value);
    refs.coordinateList.append(item);
  });
}

function updateSymmetryPanel() {
  const count = state.coords.length || state.micCount;
  const period = 360 / count;
  refs.symmetryChip.textContent = `C${count}`;
  refs.rotationPeriod.textContent = `${Number(period.toFixed(2))}°`;
  refs.mirrorAxes.textContent = `${count} 条`;
  refs.globalAmbiguity.textContent = '框架等价';
  refs.symmetryLead.textContent = `C${count} 均匀圆阵具有 ${Number(period.toFixed(2))}° 旋转周期与 ${count} 条镜像轴。固定 CH→坐标后，扫描主瓣唯一；歧义只发生在未同步旋转或镜像整个坐标框架时。`;
  refs.symmetryNote.textContent = `若只保留未标注的阵元坐标集合，整体旋转 ${Number(period.toFixed(2))}° 或其镜像会给出同构响应；这不会凭空产生 ${count} 个同值波束峰。通道重排必须同步重映射坐标，否则会出现伪峰和角度偏差。`;
}

function updateAudio() {
  if (!state.rawMix || !state.enhanced) return;
  const rawUrl = setAudioUrl('raw', encodeWav(state.rawMix, state.sampleRate));
  const enhancedUrl = setAudioUrl('enhanced', encodeWav(state.enhanced, state.sampleRate));

  refs.rawAudio.src = rawUrl;
  refs.enhancedAudio.src = enhancedUrl;
  refs.rawDownload.href = rawUrl;
  refs.enhancedDownload.href = enhancedUrl;
  refs.rawRms.textContent = `RMS ${audioRms(state.rawMix).toFixed(3)}`;
  refs.enhancedRms.textContent = `RMS ${audioRms(state.enhanced).toFixed(3)}`;
  refs.steerAngle.textContent = formatAngle(state.selectedAngle);
  drawWaveform(refs.rawWaveCanvas, state.rawMix, '#829694', 'rgba(130, 150, 148, 0.1)');
  drawWaveform(refs.enhancedWaveCanvas, state.enhanced, '#6ee7c8', 'rgba(110, 231, 200, 0.14)');
}

function setupCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(1, rect.width);
  const height = Math.max(1, rect.height);
  const ratio = Math.min(2, window.devicePixelRatio || 1);
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
  }
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  return { context, width, height };
}

function polarPoint(cx, cy, radius, angleDeg) {
  const phi = angleDeg * DEG;
  return {
    x: cx + radius * Math.cos(phi),
    y: cy - radius * Math.sin(phi)
  };
}

function dbRadius(db, inner, outer) {
  const normalized = Math.max(0, Math.min(1, 1 + db / 40));
  return inner + (outer - inner) * normalized;
}

function drawPolar() {
  const { context: ctx, width, height } = setupCanvas(refs.polarCanvas);
  const compact = width < 430;
  const margin = compact ? 28 : 42;
  const cx = width * 0.5;
  const cy = height * 0.51;
  const outer = Math.max(80, Math.min(width, height) * 0.5 - margin);
  const inner = outer * 0.2;

  const glow = ctx.createRadialGradient(cx, cy, inner, cx, cy, outer);
  glow.addColorStop(0, 'rgba(110, 231, 200, 0.025)');
  glow.addColorStop(0.72, 'rgba(110, 231, 200, 0.018)');
  glow.addColorStop(1, 'rgba(110, 231, 200, 0.07)');
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(cx, cy, outer, 0, Math.PI * 2);
  ctx.fill();

  ctx.font = "8px 'SFMono-Regular', monospace";
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  [0, -10, -20, -30].forEach((db, index) => {
    const radius = dbRadius(db, inner, outer);
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.strokeStyle = index === 0 ? 'rgba(126, 160, 156, 0.28)' : 'rgba(126, 160, 156, 0.12)';
    ctx.lineWidth = index === 0 ? 1.2 : 1;
    ctx.setLineDash(index === 0 ? [] : [2, 5]);
    ctx.stroke();
    if (index > 0) {
      const label = polarPoint(cx, cy, radius, 142);
      ctx.fillStyle = 'rgba(129, 150, 148, 0.58)';
      ctx.fillText(`${db}`, label.x, label.y);
    }
  });

  ctx.setLineDash([]);
  for (let angle = 0; angle < 360; angle += 30) {
    const point = polarPoint(cx, cy, outer, angle);
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(point.x, point.y);
    ctx.strokeStyle = angle % 90 === 0 ? 'rgba(126, 160, 156, 0.13)' : 'rgba(126, 160, 156, 0.065)';
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  ctx.setLineDash([3, 6]);
  ctx.beginPath();
  ctx.moveTo(cx - 5, cy);
  ctx.lineTo(cx + 5, cy);
  ctx.moveTo(cx, cy - 5);
  ctx.lineTo(cx, cy + 5);
  ctx.strokeStyle = 'rgba(126, 160, 156, 0.22)';
  ctx.stroke();
  ctx.setLineDash([]);

  for (let angle = 0; angle < 360; angle += 30) {
    const labelPoint = polarPoint(cx, cy, outer + (compact ? 15 : 19), angle);
    ctx.fillStyle = angle % 90 === 0 ? 'rgba(214, 231, 226, 0.7)' : 'rgba(129, 150, 148, 0.46)';
    ctx.fillText(`${angle}°`, labelPoint.x, labelPoint.y);
  }

  if (!state.scan) return;
  const responsePoints = state.scan.angles.map((angle, index) => (
    polarPoint(cx, cy, dbRadius(state.scan.db[index], inner, outer), angle)
  ));
  ctx.beginPath();
  responsePoints.forEach((point, index) => {
    if (index === 0) ctx.moveTo(point.x, point.y);
    else ctx.lineTo(point.x, point.y);
  });
  ctx.closePath();

  const fill = ctx.createRadialGradient(cx, cy, inner, cx, cy, outer);
  fill.addColorStop(0, 'rgba(110, 231, 200, 0.035)');
  fill.addColorStop(0.7, 'rgba(110, 231, 200, 0.08)');
  fill.addColorStop(1, 'rgba(110, 231, 200, 0.17)');
  ctx.fillStyle = fill;
  ctx.fill();

  const stroke = ctx.createLinearGradient(cx - outer, cy, cx + outer, cy);
  stroke.addColorStop(0, 'rgba(110, 231, 200, 0.55)');
  stroke.addColorStop(0.5, '#6ee7c8');
  stroke.addColorStop(1, 'rgba(110, 231, 200, 0.62)');
  ctx.strokeStyle = stroke;
  ctx.lineWidth = 1.8;
  ctx.shadowColor = 'rgba(110, 231, 200, 0.28)';
  ctx.shadowBlur = 8;
  ctx.stroke();
  ctx.shadowBlur = 0;

  sourceList().forEach((source, index) => {
    const point = polarPoint(cx, cy, outer, source.angleDeg);
    const innerPoint = polarPoint(cx, cy, inner, source.angleDeg);
    ctx.beginPath();
    ctx.setLineDash([2, 5]);
    ctx.moveTo(innerPoint.x, innerPoint.y);
    ctx.lineTo(point.x, point.y);
    ctx.strokeStyle = 'rgba(244, 184, 93, 0.23)';
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.beginPath();
    ctx.arc(point.x, point.y, 3.3, 0, Math.PI * 2);
    ctx.fillStyle = '#071014';
    ctx.fill();
    ctx.strokeStyle = '#f4b85d';
    ctx.lineWidth = 1.4;
    ctx.stroke();

    const labelPoint = polarPoint(cx, cy, outer + (compact ? 27 : 34), source.angleDeg);
    ctx.font = "9px 'SFMono-Regular', monospace";
    ctx.fillStyle = '#f4b85d';
    ctx.textAlign = 'center';
    ctx.fillText(`${index === 0 ? 'A' : 'B'} ${source.angleDeg.toFixed(0)}°`, labelPoint.x, labelPoint.y);
  });

  const selectedPeak = nearestPeakTo(state.selectedAngle);
  state.peaks.slice(0, 6).forEach((peak) => {
    const radius = dbRadius(peak.relativeDb, inner, outer);
    const point = polarPoint(cx, cy, radius, peak.angle);
    const selected = selectedPeak && Math.abs(peak.angle - selectedPeak.angle) < 0.2;
    if (selected) {
      const ray = polarPoint(cx, cy, outer, peak.angle);
      ctx.beginPath();
      ctx.setLineDash([3, 4]);
      ctx.moveTo(cx, cy);
      ctx.lineTo(ray.x, ray.y);
      ctx.strokeStyle = 'rgba(110, 231, 200, 0.24)';
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.beginPath();
    ctx.arc(point.x, point.y, selected ? 5.3 : 2.8, 0, Math.PI * 2);
    ctx.fillStyle = selected ? '#6ee7c8' : 'rgba(110, 231, 200, 0.66)';
    ctx.fill();
    if (selected) {
      ctx.beginPath();
      ctx.arc(point.x, point.y, 9, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(110, 231, 200, 0.26)';
      ctx.lineWidth = 1;
      ctx.stroke();
    }
  });

  if (selectedPeak) {
    const radius = dbRadius(selectedPeak.relativeDb, inner, outer);
    const point = polarPoint(cx, cy, radius, selectedPeak.angle);
    const offsetSide = Math.cos(selectedPeak.angle * DEG) >= 0 ? 1 : -1;
    ctx.textAlign = offsetSide > 0 ? 'left' : 'right';
    ctx.textBaseline = 'bottom';
    ctx.font = "600 10px 'SFMono-Regular', monospace";
    ctx.fillStyle = '#dffbf4';
    ctx.fillText(`${selectedPeak.angle.toFixed(1)}°`, point.x + offsetSide * 11, point.y - 5);
  }

  ctx.beginPath();
  ctx.arc(cx, cy, 3, 0, Math.PI * 2);
  ctx.fillStyle = '#6ee7c8';
  ctx.fill();
  ctx.beginPath();
  ctx.arc(cx, cy, 7, 0, Math.PI * 2);
  ctx.strokeStyle = 'rgba(110, 231, 200, 0.24)';
  ctx.lineWidth = 1;
  ctx.stroke();
}

function drawArray() {
  const { context: ctx, width, height } = setupCanvas(refs.arrayCanvas);
  const cx = width * 0.5;
  const cy = height * 0.51;
  const radius = Math.min(width, height) * 0.34;
  const coords = state.coords.length ? state.coords : makeCircularArray(state.micCount, 1);

  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.strokeStyle = 'rgba(110, 231, 200, 0.18)';
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 4]);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.beginPath();
  ctx.moveTo(cx - radius - 7, cy);
  ctx.lineTo(cx + radius + 7, cy);
  ctx.moveTo(cx, cy - radius - 7);
  ctx.lineTo(cx, cy + radius + 7);
  ctx.strokeStyle = 'rgba(126, 160, 156, 0.11)';
  ctx.stroke();

  coords.forEach((mic, index) => {
    const componentRadius = Math.hypot(mic.x, mic.y) || 1;
    const x = cx + mic.x / componentRadius * radius;
    const y = cy - mic.y / componentRadius * radius;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(x, y);
    ctx.strokeStyle = index < 2 ? 'rgba(110, 231, 200, 0.12)' : 'rgba(126, 160, 156, 0.06)';
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(x, y, index < 2 ? 3.4 : 2.5, 0, Math.PI * 2);
    ctx.fillStyle = index === 0 ? '#6ee7c8' : index === 1 ? '#88a9ff' : 'rgba(202, 224, 218, 0.58)';
    ctx.fill();
    if (coords.length <= 8) {
      ctx.font = "7px 'SFMono-Regular', monospace";
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = index < 2 ? 'rgba(238, 247, 243, 0.8)' : 'rgba(129, 150, 148, 0.55)';
      const labelX = cx + (x - cx) * 1.18;
      const labelY = cy + (y - cy) * 1.18;
      ctx.fillText(`${index + 1}`, labelX, labelY);
    }
  });

  ctx.beginPath();
  ctx.arc(cx, cy, 2.5, 0, Math.PI * 2);
  ctx.fillStyle = '#f4b85d';
  ctx.fill();
  ctx.font = "7px 'SFMono-Regular', monospace";
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  ctx.fillStyle = 'rgba(129, 150, 148, 0.55)';
  ctx.fillText(`${coords.length} CH`, 8, 8);
}

function drawWaveform(canvas, samples, color, fillColor) {
  const { context: ctx, width, height } = setupCanvas(canvas);
  if (!samples?.length) return;
  const middle = height * 0.5;
  const step = Math.max(1, Math.floor(samples.length / width));
  const points = [];
  for (let x = 0; x < width; x += 1) {
    const start = Math.floor(x / width * samples.length);
    const end = Math.min(samples.length, start + step);
    let min = 1;
    let max = -1;
    for (let index = start; index < end; index += 1) {
      min = Math.min(min, samples[index]);
      max = Math.max(max, samples[index]);
    }
    points.push({ x, y1: middle - max * middle * 0.82, y2: middle - min * middle * 0.82 });
  }

  ctx.beginPath();
  points.forEach((point) => {
    ctx.moveTo(point.x + 0.5, point.y1);
    ctx.lineTo(point.x + 0.5, point.y2);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(0, middle);
  ctx.lineTo(width, middle);
  ctx.strokeStyle = fillColor;
  ctx.lineWidth = 1;
  ctx.stroke();
}

function drawAll() {
  drawPolar();
  drawArray();
  if (state.rawMix) drawWaveform(refs.rawWaveCanvas, state.rawMix, '#829694', 'rgba(130, 150, 148, 0.1)');
  if (state.enhanced) drawWaveform(refs.enhancedWaveCanvas, state.enhanced, '#6ee7c8', 'rgba(110, 231, 200, 0.14)');
}

function bindEvents() {
  $$('.segment').forEach((button) => {
    button.addEventListener('click', () => setSourceMode(button.dataset.sourceMode));
  });

  $('#sourceAAngle').addEventListener('input', (event) => {
    state.sourceAngles[0] = Number(event.target.value);
    $('#sourceAAngleValue').textContent = `${state.sourceAngles[0]}°`;
  });
  $('#sourceAAngle').addEventListener('change', () => runSimulation());

  $('#sourceBAngle').addEventListener('input', (event) => {
    state.sourceAngles[1] = Number(event.target.value);
    $('#sourceBAngleValue').textContent = `${state.sourceAngles[1]}°`;
  });
  $('#sourceBAngle').addEventListener('change', () => runSimulation());

  $('#reverbEnabled').addEventListener('change', (event) => {
    state.reverbEnabled = event.target.checked;
    $('#reverbAmount').disabled = !state.reverbEnabled;
    $('#reverbSliderRow').closest('.control-section').classList.toggle('is-disabled', !state.reverbEnabled);
    runSimulation();
  });
  $('#reverbAmount').addEventListener('input', (event) => {
    state.reverbAmount = Number(event.target.value) / 100;
    $('#reverbAmountValue').textContent = `${Math.round(state.reverbAmount * 100)}%`;
  });
  $('#reverbAmount').addEventListener('change', () => runSimulation());

  ['#micCount', '#arrayRadius', '#soundSpeed'].forEach((selector) => {
    $(selector).addEventListener('change', () => {
      syncSettingsFromControls();
      runSimulation();
    });
  });

  refs.runButton.addEventListener('click', () => {
    syncSettingsFromControls();
    runSimulation({ announce: true });
  });

  $('#swapDataButton').addEventListener('click', () => {
    const nextMode = state.mappingMode === 'unmapped' ? 'identity' : 'unmapped';
    applyMapping(
      nextMode,
      nextMode === 'unmapped'
        ? '已交换 CH1/CH2 数据，但坐标未重排，映射体检将标记异常'
        : '已撤销数据交换，恢复原始通道顺序'
    );
  });

  $('#remapButton').addEventListener('click', () => {
    const nextMode = state.mappingMode === 'remapped' ? 'identity' : 'remapped';
    applyMapping(
      nextMode,
      nextMode === 'remapped'
        ? '数据与坐标已同步交换，映射保持一致'
        : '已恢复原始通道顺序与坐标'
    );
  });

  $('#restoreButton').addEventListener('click', () => {
    applyMapping('identity', '已恢复原始通道顺序');
  });

  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(drawAll, 90);
  });

  window.addEventListener('keydown', (event) => {
    if (event.key.toLowerCase() !== 'r' || event.metaKey || event.ctrlKey || event.altKey) return;
    const tag = event.target.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
    event.preventDefault();
    syncSettingsFromControls();
    runSimulation({ announce: true });
  });
}

function clampSettings() {
  const micCount = Number($('#micCount').value);
  const radiusCm = Number($('#arrayRadius').value);
  const speed = Number($('#soundSpeed').value);
  state.micCount = [6, 8, 12].includes(micCount) ? micCount : 8;
  state.radius = Math.max(0.06, Math.min(0.2, (Number.isFinite(radiusCm) ? radiusCm : 12) / 100));
  state.soundSpeed = Math.max(300, Math.min(380, Number.isFinite(speed) ? speed : 343));
  $('#arrayRadius').value = String(Math.round(state.radius * 100));
  $('#soundSpeed').value = String(Math.round(state.soundSpeed));
}

function init() {
  syncSettingsFromControls();
  clampSettings();
  updateRangeLabels();
  $('#reverbAmount').disabled = !state.reverbEnabled;
  $('#reverbSliderRow').closest('.control-section').classList.toggle('is-disabled', !state.reverbEnabled);
  bindEvents();
  runSimulation();
}

init();
