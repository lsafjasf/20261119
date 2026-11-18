import {
  FIR_PRESETS,
  SAMPLE_RATE,
  analyzeSweep,
  nextPowerOfTwo,
} from './dsp.js';

const $ = (id) => document.getElementById(id);
const elements = {
  form: $('analyzerForm'),
  f1Input: $('f1Input'),
  f2Input: $('f2Input'),
  durationInput: $('durationInput'),
  presetInput: $('presetInput'),
  nonlinearInput: $('nonlinearInput'),
  k2Input: $('k2Input'),
  k2Field: $('k2Field'),
  f1Output: $('f1Output'),
  f2Output: $('f2Output'),
  durationOutput: $('durationOutput'),
  k2Output: $('k2Output'),
  tbpOutput: $('tbpOutput'),
  tapList: $('tapList'),
  directDelayOutput: $('directDelayOutput'),
  firSpanOutput: $('firSpanOutput'),
  tapCountLabel: $('tapCountLabel'),
  analyzeButton: $('analyzeButton'),
  busyOverlay: $('busyOverlay'),
  engineStatus: $('engineStatus'),
  fftSizeOutput: $('fftSizeOutput'),
  analysisTimeOutput: $('analysisTimeOutput'),
  delayErrorKpi: $('delayErrorKpi'),
  delayFoot: $('delayFoot'),
  magnitudeKpi: $('magnitudeKpi'),
  magnitudeFoot: $('magnitudeFoot'),
  h2Kpi: $('h2Kpi'),
  h2Foot: $('h2Foot'),
  leakKpi: $('leakKpi'),
  leakFoot: $('leakFoot'),
  sweepCanvas: $('sweepCanvas'),
  inverseCanvas: $('inverseCanvas'),
  irCanvas: $('irCanvas'),
  responseCanvas: $('responseCanvas'),
  sweepMeta: $('sweepMeta'),
  sweepLengthMeta: $('sweepLengthMeta'),
  inverseLengthMeta: $('inverseLengthMeta'),
  peakBadge: $('peakBadge'),
  windowStateTag: $('windowStateTag'),
  irRangeMeta: $('irRangeMeta'),
  errorBandMeta: $('errorBandMeta'),
  h1Bar: $('h1Bar'),
  h2Bar: $('h2Bar'),
  h1EnergyValue: $('h1EnergyValue'),
  h2EnergyValue: $('h2EnergyValue'),
  roomWindowRange: $('roomWindowRange'),
  roomWindowEnergy: $('roomWindowEnergy'),
  h2WindowRange: $('h2WindowRange'),
  h2WindowEnergy: $('h2WindowEnergy'),
  leakDetail: $('leakDetail'),
  linearLeakDetail: $('linearLeakDetail'),
};

let currentResult = null;
let scheduledRun = null;
let renderQueued = false;
let analysisRunning = false;
let analysisQueued = false;

const COLORS = {
  text: '#edf7f5',
  muted: '#7f9d9a',
  grid: 'rgba(154, 196, 192, 0.085)',
  gridStrong: 'rgba(154, 196, 192, 0.15)',
  mint: '#5eead4',
  mintBright: '#9bf6e6',
  amber: '#f6bd60',
  amberBright: '#ffd894',
  coral: '#ff7c83',
};

function formatNumber(value, digits = 1) {
  return Number(value).toFixed(digits).replace('-', '−');
}

function formatSigned(value, digits = 1) {
  const rounded = Math.abs(value) < 0.5 * 10 ** -digits ? 0 : value;
  return `${rounded > 0 ? '+' : rounded < 0 ? '−' : '+'}${Math.abs(rounded).toFixed(digits)}`;
}

function formatHz(value) {
  return value >= 1000 ? `${(value / 1000).toFixed(1)} kHz` : `${Math.round(value)} Hz`;
}

function formatRange(start, end) {
  return `${formatSigned(start, 1)} → ${formatSigned(end, 1)} ms`;
}

function syncParameterLabels() {
  const f1 = Number(elements.f1Input.value);
  const f2 = Number(elements.f2Input.value);
  const duration = Number(elements.durationInput.value);
  elements.f1Output.textContent = formatHz(f1);
  elements.f2Output.textContent = formatHz(f2);
  elements.durationOutput.textContent = `${duration.toFixed(2)} s`;
  elements.k2Output.textContent = Number(elements.k2Input.value).toFixed(3);
  elements.tbpOutput.textContent = `${(Math.log2(f2 / f1) * duration).toFixed(2)} oct`;
  elements.k2Field.style.opacity = elements.nonlinearInput.checked ? '1' : '0.38';
  elements.k2Input.disabled = !elements.nonlinearInput.checked;
}

function updateFirSummary() {
  const preset = FIR_PRESETS[elements.presetInput.value] ?? FIR_PRESETS.studio;
  const taps = [
    { label: '直达', offsetMs: 0, gain: 1, direct: true },
    ...preset.reflections.map((tap) => ({ ...tap, label: '反射' })),
  ];
  elements.tapList.replaceChildren(...taps.map((tap) => {
    const row = document.createElement('div');
    row.className = `tap-item${tap.direct ? ' direct' : ' reflection'}`;
    const dot = document.createElement('i');
    dot.className = 'tap-dot';
    const label = document.createElement('span');
    label.textContent = `${tap.label} ${formatSigned(tap.offsetMs, 1)} ms`;
    const gain = document.createElement('span');
    gain.className = 'tap-gain';
    gain.textContent = `${tap.gain >= 0 ? '+' : '−'}${Math.abs(tap.gain).toFixed(2)}`;
    row.append(dot, label, gain);
    return row;
  }));
  elements.directDelayOutput.textContent = `${preset.directMs.toFixed(2)} ms`;
  elements.firSpanOutput.textContent = `${preset.reflections.at(-1).offsetMs.toFixed(1)} ms`;
  elements.tapCountLabel.textContent = `${taps.length} taps`;
}

function readConfig() {
  return {
    f1: Number(elements.f1Input.value),
    f2: Number(elements.f2Input.value),
    duration: Number(elements.durationInput.value),
    presetId: elements.presetInput.value,
    nonlinear: elements.nonlinearInput.checked,
    k2: Number(elements.k2Input.value),
  };
}

function updateInterface(result) {
  const { metrics, parameters, sweep, inverse, response, windows, roomGate, h2 } = result;
  const nonlinActive = parameters.nonlinear && parameters.k2 > 0;

  elements.delayErrorKpi.innerHTML = `${formatSigned(metrics.peakDelayErrorMs, 3)} <small>ms</small>`;
  elements.delayFoot.textContent = `真值 ${metrics.truePeakDelayMs.toFixed(3)} / 恢复 ${metrics.recoveredPeakDelayMs.toFixed(3)} ms`;
  elements.magnitudeKpi.innerHTML = `${formatNumber(metrics.magnitudeRmsDb, 2)} <small>dB</small>`;
  elements.magnitudeFoot.textContent = `1/12 oct · ${formatHz(response.compareFrom)}–${formatHz(response.compareTo)} · max ${metrics.magnitudeMaxErrorDb.toFixed(2)} dB`;

  if (nonlinActive) {
    elements.h2Kpi.innerHTML = `${formatNumber(metrics.h2RelativeDb, 1)} <small>dB</small>`;
    elements.h2Foot.textContent = `相对 H₁ · ${formatRange(windows.h2StartMs, windows.h2EndMs)}`;
    elements.leakKpi.innerHTML = `${formatNumber(metrics.h2LeakagePeakDb, 1)} <small>dB</small>`;
    elements.leakFoot.textContent = `峰值串扰 · 余量 ${metrics.separationMarginMs.toFixed(1)} ms`;
    elements.leakKpi.style.color = metrics.h2LeakagePeakDb < -25 ? 'var(--mint-bright)' : 'var(--coral)';
  } else {
    elements.h2Kpi.innerHTML = '<small>未启用</small>';
    elements.h2Kpi.style.color = 'var(--muted-bright)';
    elements.h2Foot.textContent = 'k₂ = 0 · 仅线性样例';
    elements.leakKpi.innerHTML = '<small>—</small>';
    elements.leakKpi.style.color = 'var(--muted-bright)';
    elements.leakFoot.textContent = '开启二次非线性后报告';
  }

  elements.sweepMeta.textContent = `${formatHz(parameters.f1)} → ${formatHz(parameters.f2)} · T ${sweep.duration.toFixed(2)} s`;
  elements.sweepLengthMeta.textContent = `${sweep.samples.length.toLocaleString('en-US')} samples`;
  elements.inverseLengthMeta.textContent = `${inverse.samples.length.toLocaleString('en-US')} samples`;
  elements.peakBadge.textContent = `${formatSigned(result.mainPeak.delayMs, 3)} ms`;
  elements.fftSizeOutput.textContent = `${nextPowerOfTwo(result.linearOutput.length + inverse.samples.length - 1).toLocaleString('en-US')} pt`;
  elements.analysisTimeOutput.textContent = `${result.analysisMs.toFixed(0)} ms`;

  const margin = metrics.separationMarginMs;
  if (!nonlinActive) {
    elements.windowStateTag.textContent = '线性样例';
    elements.windowStateTag.className = 'tag';
  } else if (margin > 8) {
    elements.windowStateTag.textContent = 'H₂ 可分离';
    elements.windowStateTag.className = 'tag tag-warm';
  } else if (margin > 0) {
    elements.windowStateTag.textContent = '分离临界';
    elements.windowStateTag.className = 'tag';
  } else {
    elements.windowStateTag.textContent = '窗口重叠';
    elements.windowStateTag.className = 'tag';
  }

  elements.irRangeMeta.textContent = `门控 ${formatRange(windows.mainStartMs, windows.mainEndMs)}`;
  elements.errorBandMeta.textContent = `RMS ${metrics.magnitudeRmsDb.toFixed(2)} dB · max ${metrics.magnitudeMaxErrorDb.toFixed(2)} dB`;
  elements.h1Bar.style.width = '100%';
  elements.h1EnergyValue.textContent = '0.0 dB';
  if (nonlinActive) {
    const barWidth = Math.max(3, Math.min(100, (metrics.h2RelativeDb + 60) / 60 * 100));
    elements.h2Bar.style.width = `${barWidth.toFixed(1)}%`;
    elements.h2EnergyValue.textContent = `${formatNumber(metrics.h2RelativeDb, 1)} dBc`;
    elements.roomWindowEnergy.textContent = `E ${metrics.h1Energy.toFixed(2)}`;
    elements.h2WindowEnergy.textContent = `${formatNumber(metrics.h2RelativeDb, 1)} dBc`;
    elements.leakDetail.textContent = `${metrics.h2LeakagePeakDb.toFixed(1)} dB pk / ${metrics.h2LeakageIntoRoomDb.toFixed(1)} dB E`;
    elements.linearLeakDetail.textContent = `${metrics.linearLeakagePeakDb.toFixed(1)} dB pk / ${metrics.linearLeakageIntoH2Db.toFixed(1)} dB E`;
  } else {
    elements.h2Bar.style.width = '0%';
    elements.h2EnergyValue.textContent = '—';
    elements.roomWindowEnergy.textContent = `E ${metrics.h1Energy.toFixed(2)}`;
    elements.h2WindowEnergy.textContent = '—';
    elements.leakDetail.textContent = '—';
    elements.linearLeakDetail.textContent = '—';
  }
  elements.roomWindowRange.textContent = formatRange(windows.mainStartMs, windows.mainEndMs);
  elements.h2WindowRange.textContent = formatRange(windows.h2StartMs, windows.h2EndMs);
  elements.h2WindowRange.title = nonlinActive
    ? `H₂ 理论提前量 ${h2.deltaMs.toFixed(1)} ms`
    : '未启用二次非线性';
  elements.roomWindowRange.title = `样本 ${roomGate.start}–${roomGate.end}`;
}

async function runAnalysis() {
  if (analysisRunning) {
    analysisQueued = true;
    return;
  }
  analysisRunning = true;
  elements.busyOverlay.hidden = false;
  elements.engineStatus.textContent = '正在分析';
  document.querySelector('.status-pill').classList.add('busy');
  await new Promise((resolve) => setTimeout(resolve, 35));
  try {
    currentResult = analyzeSweep(readConfig());
    updateInterface(currentResult);
    renderAll();
    elements.engineStatus.textContent = '分析完成';
  } catch (error) {
    console.error(error);
    elements.engineStatus.textContent = '分析失败';
  } finally {
    elements.busyOverlay.hidden = true;
    document.querySelector('.status-pill').classList.remove('busy');
    analysisRunning = false;
    if (analysisQueued) {
      analysisQueued = false;
      runAnalysis();
    }
  }
}

function scheduleAnalysis(delay = 180) {
  clearTimeout(scheduledRun);
  scheduledRun = setTimeout(runAnalysis, delay);
}

function setupControls() {
  for (const input of [
    elements.f1Input,
    elements.f2Input,
    elements.durationInput,
    elements.k2Input,
  ]) {
    input.addEventListener('input', syncParameterLabels);
    input.addEventListener('change', () => scheduleAnalysis());
  }
  elements.presetInput.addEventListener('change', () => {
    updateFirSummary();
    scheduleAnalysis();
  });
  elements.nonlinearInput.addEventListener('change', () => {
    syncParameterLabels();
    scheduleAnalysis(60);
  });
  elements.form.addEventListener('submit', (event) => {
    event.preventDefault();
    clearTimeout(scheduledRun);
    runAnalysis();
  });
}

function queueRender() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => {
    renderQueued = false;
    if (currentResult) renderAll();
  });
}

function renderAll() {
  if (!currentResult) return;
  drawWaveform(elements.sweepCanvas, currentResult.sweep.samples, currentResult.sampleRate, COLORS.mint);
  drawWaveform(elements.inverseCanvas, currentResult.inverse.samples, currentResult.sampleRate, COLORS.amber);
  drawImpulseResponse(currentResult);
  drawResponse(currentResult);
}

function prepareCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(1, rect.width);
  const height = Math.max(1, rect.height);
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const pixelWidth = Math.round(width * ratio);
  const pixelHeight = Math.round(height * ratio);
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
    canvas.width = pixelWidth;
    canvas.height = pixelHeight;
  }
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  return { context, width, height };
}

function drawHorizontalGrid(context, plot, count = 4) {
  context.save();
  context.strokeStyle = COLORS.grid;
  context.lineWidth = 1;
  context.beginPath();
  for (let i = 0; i <= count; i++) {
    const y = plot.top + plot.height * i / count + 0.5;
    context.moveTo(plot.left, y);
    context.lineTo(plot.left + plot.width, y);
  }
  context.stroke();
  context.restore();
}

function drawWaveform(canvas, samples, sampleRate, color) {
  const { context, width, height } = prepareCanvas(canvas);
  if (width < 20 || height < 20) return;
  const plot = {
    left: 11,
    top: 12,
    width: width - 19,
    height: height - 32,
  };
  const middle = plot.top + plot.height / 2;
  drawHorizontalGrid(context, plot, 3);
  context.save();
  context.strokeStyle = 'rgba(154, 196, 192, 0.14)';
  context.beginPath();
  context.moveTo(plot.left, middle + 0.5);
  context.lineTo(plot.left + plot.width, middle + 0.5);
  context.stroke();

  const columns = Math.max(1, Math.floor(plot.width));
  const top = new Float32Array(columns).fill(-1);
  const bottom = new Float32Array(columns).fill(1);
  let globalPeak = 1e-12;
  for (let i = 0; i < samples.length; i++) {
    globalPeak = Math.max(globalPeak, Math.abs(samples[i]));
    const column = Math.min(columns - 1, Math.floor(i * columns / samples.length));
    const value = samples[i] / globalPeak;
    top[column] = Math.max(top[column], value);
    bottom[column] = Math.min(bottom[column], value);
  }

  const gradient = context.createLinearGradient(plot.left, 0, plot.left + plot.width, 0);
  gradient.addColorStop(0, `${color}24`);
  gradient.addColorStop(0.55, `${color}4d`);
  gradient.addColorStop(1, `${color}22`);
  context.fillStyle = gradient;
  context.beginPath();
  context.moveTo(plot.left, middle - top[0] * plot.height * 0.43);
  for (let i = 1; i < columns; i++) {
    const x = plot.left + i;
    context.lineTo(x, middle - top[i] * plot.height * 0.43);
  }
  for (let i = columns - 1; i >= 0; i--) {
    const x = plot.left + i;
    context.lineTo(x, middle - bottom[i] * plot.height * 0.43);
  }
  context.closePath();
  context.fill();

  context.strokeStyle = `${color}a8`;
  context.lineWidth = 0.8;
  context.shadowColor = `${color}5c`;
  context.shadowBlur = 7;
  context.beginPath();
  for (let i = 0; i < columns; i++) {
    const x = plot.left + i;
    const y = middle - top[i] * plot.height * 0.43;
    if (i === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  }
  context.stroke();
  context.restore();

  const duration = samples.length / sampleRate;
  context.fillStyle = COLORS.muted;
  context.font = '8px "Noto Sans SC", "SFMono-Regular", Consolas, monospace';
  context.textBaseline = 'top';
  context.fillText('0', plot.left, plot.top + plot.height + 8);
  context.textAlign = 'center';
  context.fillText(`${(duration / 2).toFixed(2)} s`, plot.left + plot.width / 2, plot.top + plot.height + 8);
  context.textAlign = 'right';
  context.fillText(`${duration.toFixed(2)} s`, plot.left + plot.width, plot.top + plot.height + 8);
  context.textAlign = 'left';
  context.fillStyle = 'rgba(127, 157, 154, 0.7)';
  context.fillText('+1', plot.left, plot.top - 1);
  context.fillText('−1', plot.left, plot.top + plot.height - 9);
}

function impulseEnvelope(samples, referenceIndex, fromIndex, toIndex, xMin, xMax, columns, peakValue) {
  const envelope = new Float32Array(columns).fill(-120);
  const from = Math.max(0, Math.floor(fromIndex));
  const to = Math.min(samples.length, Math.ceil(toIndex));
  for (let i = from; i < to; i++) {
    const relativeMs = (i - referenceIndex) * 1000 / SAMPLE_RATE;
    const column = Math.min(
      columns - 1,
      Math.floor((relativeMs - xMin) / (xMax - xMin) * columns),
    );
    const db = 20 * Math.log10(Math.max(1e-12, Math.abs(samples[i]) / peakValue));
    if (db > envelope[column]) envelope[column] = db;
  }
  return envelope;
}

function drawEnvelopeLine(context, envelope, plot, xMin, xMax, yMin, yMax, color, dashed = false) {
  context.save();
  context.strokeStyle = color;
  context.lineWidth = dashed ? 0.8 : 1.25;
  context.setLineDash(dashed ? [3, 4] : []);
  context.beginPath();
  let started = false;
  for (let i = 0; i < envelope.length; i++) {
    const value = envelope[i];
    if (value < yMin - 2) {
      started = false;
      continue;
    }
    const x = plot.left + i + 0.5;
    const y = plot.top + (yMax - Math.min(yMax, value)) / (yMax - yMin) * plot.height;
    if (!started) {
      context.moveTo(x, y);
      started = true;
    } else {
      context.lineTo(x, y);
    }
  }
  context.stroke();
  context.restore();
}

function drawImpulseResponse(result) {
  const { context, width, height } = prepareCanvas(elements.irCanvas);
  if (width < 40 || height < 40) return;
  const plot = {
    left: 43,
    top: 24,
    width: width - 57,
    height: height - 55,
  };
  const xMin = -Math.max(55, -result.windows.h2StartMs + 10);
  const xMax = Math.max(35, result.windows.mainEndMs + 14);
  const yMin = -80;
  const yMax = 5;
  const columns = Math.max(1, Math.floor(plot.width));
  const mainIndex = result.mainPeak.index;
  const peakValue = Math.max(1e-12, result.mainPeak.value);
  const allStart = mainIndex + Math.floor(xMin * SAMPLE_RATE / 1000);
  const allEnd = mainIndex + Math.ceil(xMax * SAMPLE_RATE / 1000);

  const xToPixel = (relativeMs) =>
    plot.left + (relativeMs - xMin) / (xMax - xMin) * plot.width;
  const yToPixel = (db) =>
    plot.top + (yMax - Math.min(yMax, Math.max(yMin, db))) / (yMax - yMin) * plot.height;

  context.save();
  context.beginPath();
  context.rect(plot.left, plot.top, plot.width, plot.height);
  context.clip();

  const roomRight = xToPixel(result.windows.mainEndMs);
  const roomLeft = xToPixel(result.windows.mainStartMs);
  context.fillStyle = 'rgba(94, 234, 212, 0.075)';
  context.fillRect(roomLeft, plot.top, roomRight - roomLeft, plot.height);
  const h2Left = xToPixel(result.windows.h2StartMs);
  const h2Right = xToPixel(result.windows.h2EndMs);
  context.fillStyle = 'rgba(246, 189, 96, 0.085)';
  context.fillRect(h2Left, plot.top, h2Right - h2Left, plot.height);

  for (let db = yMin; db <= yMax; db += 20) {
    const y = yToPixel(db);
    context.strokeStyle = db === 0 ? COLORS.gridStrong : COLORS.grid;
    context.beginPath();
    context.moveTo(plot.left, y + 0.5);
    context.lineTo(plot.left + plot.width, y + 0.5);
    context.stroke();
  }

  const tickStep = xMax - xMin > 220 ? 50 : 25;
  context.font = '8px "Noto Sans SC", "SFMono-Regular", Consolas, monospace';
  context.textAlign = 'center';
  context.textBaseline = 'top';
  for (let tick = Math.ceil(xMin / tickStep) * tickStep; tick <= xMax; tick += tickStep) {
    const x = xToPixel(tick);
    context.strokeStyle = COLORS.grid;
    context.beginPath();
    context.moveTo(x + 0.5, plot.top);
    context.lineTo(x + 0.5, plot.top + plot.height);
    context.stroke();
    context.fillStyle = COLORS.muted;
    context.fillText(`${tick > 0 ? '+' : ''}${tick}`, x, plot.top + plot.height + 8);
  }

  const measuredEnvelope = impulseEnvelope(
    result.measuredIR,
    mainIndex,
    allStart,
    allEnd,
    xMin,
    xMax,
    columns,
    peakValue,
  );
  const linearEnvelope = impulseEnvelope(
    result.linearIR,
    mainIndex,
    allStart,
    allEnd,
    xMin,
    xMax,
    columns,
    peakValue,
  );
  drawEnvelopeLine(context, linearEnvelope, plot, xMin, xMax, yMin, yMax, 'rgba(168, 193, 190, 0.55)', true);
  drawEnvelopeLine(context, measuredEnvelope, plot, xMin, xMax, yMin, yMax, COLORS.mintBright);

  if (result.parameters.nonlinear && result.parameters.k2 > 0) {
    const h2Envelope = impulseEnvelope(
      result.distortionIR,
      mainIndex,
      result.h2.window.start,
      result.h2.window.end,
      xMin,
      xMax,
      columns,
      peakValue,
    );
    drawEnvelopeLine(context, h2Envelope, plot, xMin, xMax, yMin, yMax, COLORS.amberBright);
  }

  const mainX = xToPixel(0);
  context.strokeStyle = 'rgba(94, 234, 212, 0.7)';
  context.lineWidth = 1;
  context.setLineDash([2, 4]);
  context.beginPath();
  context.moveTo(mainX + 0.5, plot.top);
  context.lineTo(mainX + 0.5, plot.top + plot.height);
  context.stroke();
  context.setLineDash([]);

  if (result.parameters.nonlinear && result.parameters.k2 > 0) {
    const h2X = xToPixel(result.h2.peakDelayMs);
    context.strokeStyle = 'rgba(246, 189, 96, 0.62)';
    context.beginPath();
    context.moveTo(h2X + 0.5, plot.top);
    context.lineTo(h2X + 0.5, plot.top + plot.height);
    context.stroke();
  }

  for (const tap of result.fir.taps) {
    const x = xToPixel(tap.offsetMs);
    context.fillStyle = tap.kind === 'direct' ? COLORS.mint : 'rgba(94, 234, 212, 0.34)';
    context.beginPath();
    context.arc(x, plot.top + plot.height - 5, tap.kind === 'direct' ? 2.5 : 1.7, 0, Math.PI * 2);
    context.fill();
  }
  context.restore();

  context.font = '8px "Noto Sans SC", "SFMono-Regular", Consolas, monospace';
  context.textAlign = 'right';
  context.textBaseline = 'middle';
  for (let db = yMin; db <= yMax; db += 20) {
    context.fillStyle = db === 0 ? COLORS.mint : COLORS.muted;
    context.fillText(`${db > 0 ? '+' : ''}${db}`, plot.left - 7, yToPixel(db));
  }
  context.textAlign = 'left';
  context.fillStyle = 'rgba(127, 157, 154, 0.7)';
  context.fillText('dB', 8, plot.top - 14);
  context.textAlign = 'center';
  context.fillText('相对卷积峰 (ms)', plot.left + plot.width / 2, height - 12);

  context.fillStyle = COLORS.mintBright;
  context.textAlign = mainX > plot.left + plot.width * 0.7 ? 'right' : 'left';
  context.fillText(
    `H₁ ${formatSigned(result.mainPeak.delayMs, 3)} ms`,
    mainX + (context.textAlign === 'right' ? -6 : 6),
    plot.top + 8,
  );
  if (result.parameters.nonlinear && result.parameters.k2 > 0) {
    const h2X = xToPixel(result.h2.peakDelayMs);
    context.fillStyle = COLORS.amberBright;
    context.textAlign = 'center';
    context.fillText(`H₂ ${formatSigned(result.h2.peakDelayMs, 1)} ms`, h2X, plot.top + 18);
  }
  context.fillStyle = 'rgba(94, 234, 212, 0.66)';
  context.textAlign = 'left';
  context.fillText('房间门控', Math.max(plot.left + 4, roomLeft + 5), plot.top + plot.height - 13);
  if (result.parameters.nonlinear && result.parameters.k2 > 0) {
    context.fillStyle = 'rgba(246, 189, 96, 0.7)';
    context.fillText('H₂ 分离窗', Math.max(plot.left + 4, h2Left + 5), plot.top + plot.height - 13);
  }
}

function drawResponse(result) {
  const { context, width, height } = prepareCanvas(elements.responseCanvas);
  if (width < 40 || height < 40) return;
  const curve = result.response.curve;
  const plot = {
    left: 41,
    top: 16,
    width: width - 56,
    height: height - 42,
  };
  const fMin = result.parameters.f1;
  const fMax = result.parameters.f2;
  const values = curve.flatMap((point) => [point.trueDb, point.recoveredDb]);
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const yMin = Math.max(-55, Math.floor((rawMin - 3) / 10) * 10);
  const yMax = Math.min(20, Math.ceil((rawMax + 3) / 5) * 5);
  const xToPixel = (frequency) =>
    plot.left + Math.log(frequency / fMin) / Math.log(fMax / fMin) * plot.width;
  const yToPixel = (db) =>
    plot.top + (yMax - Math.min(yMax, Math.max(yMin, db))) / (yMax - yMin) * plot.height;

  context.save();
  context.beginPath();
  context.rect(plot.left, plot.top, plot.width, plot.height);
  context.clip();
  for (let db = yMin; db <= yMax; db += 10) {
    const y = yToPixel(db);
    context.strokeStyle = db === 0 ? COLORS.gridStrong : COLORS.grid;
    context.beginPath();
    context.moveTo(plot.left, y + 0.5);
    context.lineTo(plot.left + plot.width, y + 0.5);
    context.stroke();
  }

  const tickFrequencies = [];
  for (let frequency = fMin; frequency <= fMax; frequency *= 2) tickFrequencies.push(frequency);
  for (const frequency of [100, 1000, 10000]) {
    if (frequency > fMin * 1.05 && frequency < fMax * 0.95) tickFrequencies.push(frequency);
  }
  tickFrequencies.sort((a, b) => a - b);
  context.font = '8px "Noto Sans SC", "SFMono-Regular", Consolas, monospace';
  context.textAlign = 'center';
  context.textBaseline = 'top';
  for (const frequency of tickFrequencies) {
    const x = xToPixel(frequency);
    context.strokeStyle = COLORS.grid;
    context.beginPath();
    context.moveTo(x + 0.5, plot.top);
    context.lineTo(x + 0.5, plot.top + plot.height);
    context.stroke();
    context.fillStyle = COLORS.muted;
    const label = frequency >= 1000 ? `${(frequency / 1000).toFixed(frequency >= 10000 ? 0 : 1)}k` : `${Math.round(frequency)}`;
    context.fillText(label, x, plot.top + plot.height + 8);
  }

  context.beginPath();
  curve.forEach((point, index) => {
    const x = xToPixel(point.frequency);
    const y = yToPixel(point.trueDb);
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  for (let i = curve.length - 1; i >= 0; i--) {
    context.lineTo(xToPixel(curve[i].frequency), yToPixel(curve[i].recoveredDb));
  }
  context.closePath();
  context.fillStyle = 'rgba(246, 189, 96, 0.045)';
  context.fill();

  context.strokeStyle = 'rgba(237, 247, 245, 0.72)';
  context.lineWidth = 1.1;
  context.beginPath();
  curve.forEach((point, index) => {
    const x = xToPixel(point.frequency);
    const y = yToPixel(point.trueDb);
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.stroke();

  context.strokeStyle = COLORS.mint;
  context.lineWidth = 1.35;
  context.shadowColor = 'rgba(94, 234, 212, 0.5)';
  context.shadowBlur = 5;
  context.beginPath();
  curve.forEach((point, index) => {
    const x = xToPixel(point.frequency);
    const y = yToPixel(point.recoveredDb);
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.stroke();
  context.restore();

  context.font = '8px "Noto Sans SC", "SFMono-Regular", Consolas, monospace';
  context.textAlign = 'right';
  context.textBaseline = 'middle';
  for (let db = yMin; db <= yMax; db += 10) {
    context.fillStyle = db === 0 ? COLORS.mint : COLORS.muted;
    context.fillText(`${db > 0 ? '+' : ''}${db}`, plot.left - 7, yToPixel(db));
  }
  context.textAlign = 'left';
  context.fillStyle = 'rgba(127, 157, 154, 0.7)';
  context.fillText('dB', 7, plot.top - 9);
  context.textAlign = 'right';
  context.fillText('Hz', plot.left + plot.width, plot.top - 9);
}

function setupObservers() {
  if ('ResizeObserver' in window) {
    const observer = new ResizeObserver(queueRender);
    for (const canvas of [
      elements.sweepCanvas,
      elements.inverseCanvas,
      elements.irCanvas,
      elements.responseCanvas,
    ]) observer.observe(canvas);
  } else {
    window.addEventListener('resize', queueRender);
  }
}

syncParameterLabels();
updateFirSummary();
setupControls();
setupObservers();
runAnalysis();
