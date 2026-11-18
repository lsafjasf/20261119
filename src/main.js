import { RealtimeGranularAudio } from "./audio-controller.js";
import {
  downmixToMono,
  makeConstantToneSource,
  makeImpulseSource,
  resampleLinear
} from "./dsp-utils.js";
import { runAllDiagnostics } from "./diagnostics.js";
import { GrainView } from "./grain-view.js";
import { pcmToStereoWav, renderGranularPcm } from "./offline-render.js";
import { WaveformView } from "./waveform-view.js";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const dom = {
  playBtn: $("#playBtn"),
  playLabel: $("#playLabel"),
  stopBtn: $("#stopBtn"),
  engineBadge: $("#engineBadge"),
  engineMode: $("#engineMode"),
  loadBtn: $("#loadBtn"),
  fileInput: $("#fileInput"),
  exportBtn: $("#exportBtn"),
  exportWideBtn: $("#exportWideBtn"),
  sourceName: $("#sourceName"),
  sourceMeta: $("#sourceMeta"),
  sourceEndLabel: $("#sourceEndLabel"),
  freezeTag: $("#freezeTag"),
  positionReadout: $("#positionReadout"),
  freezeBtn: $("#freezeBtn"),
  freezeState: $("#freezeState"),
  positionRange: $("#positionRange"),
  positionValue: $("#positionValue"),
  scanRate: $("#scanRate"),
  scanRateValue: $("#scanRateValue"),
  grainLength: $("#grainLength"),
  grainLengthValue: $("#grainLengthValue"),
  windowButtons: $("#windowButtons"),
  emissionInterval: $("#emissionInterval"),
  emissionIntervalValue: $("#emissionIntervalValue"),
  densityState: $("#densityState"),
  overlapValue: $("#overlapValue"),
  triggersValue: $("#triggersValue"),
  maxVoices: $("#maxVoices"),
  maxVoicesValue: $("#maxVoicesValue"),
  masterGain: $("#masterGain"),
  masterGainValue: $("#masterGainValue"),
  metricActive: $("#metricActive"),
  metricDensity: $("#metricDensity"),
  metricOverlap: $("#metricOverlap"),
  metricRejected: $("#metricRejected"),
  activeCountState: $("#activeCountState"),
  policyRejected: $("#policyRejected"),
  poolStatusWrap: $(".pool-status"),
  poolStatus: $("#poolStatus"),
  cloudEmpty: $("#cloudEmpty"),
  runDiagnostics: $("#runDiagnostics"),
  diagnosticsGrid: $("#diagnosticsGrid"),
  exportDialog: $("#exportDialog"),
  closeExport: $("#closeExport"),
  cancelExport: $("#cancelExport"),
  exportDuration: $("#exportDuration"),
  exportDurationValue: $("#exportDurationValue"),
  exportSummary: $("#exportSummary"),
  exportStatus: $("#exportStatus"),
  exportPercent: $("#exportPercent"),
  exportProgressBar: $("#exportProgressBar"),
  confirmExport: $("#confirmExport"),
  toast: $("#toast")
};

const waveformView = new WaveformView($("#waveCanvas"));
const grainView = new GrainView($("#grainCanvas"));

const state = {
  source: new Float32Array(0),
  sourceRate: 48000,
  sourceName: "Constant tone",
  preset: "tone",
  duration: 0,
  position: 0,
  frozen: false,
  playing: false,
  status: null,
  statusAt: 0,
  lastSeekAt: 0,
  rejectedFlashUntil: 0,
  exporting: false,
  settings: {
    grainLengthSeconds: 0.09,
    emissionIntervalSeconds: 0.03,
    windowType: "hann",
    maxVoices: 32,
    masterGain: 0.8,
    scanRate: 1,
    clipEnabled: true,
    frozen: false,
    readPositionSeconds: 0
  }
};

const audio = new RealtimeGranularAudio({
  onStatus: handleEngineStatus,
  onError: (message) => showToast(message, true)
});

let toastTimer = 0;
let statusRejected = 0;

function showToast(message, isError = false) {
  window.clearTimeout(toastTimer);
  dom.toast.textContent = message;
  dom.toast.classList.toggle("error", isError);
  dom.toast.classList.add("visible");
  toastTimer = window.setTimeout(() => dom.toast.classList.remove("visible"), 3200);
}

function formatSeconds(seconds, digits = 3) {
  return `${Math.max(0, seconds || 0).toFixed(digits)} s`;
}

function formatTimecode(seconds) {
  const safe = Math.max(0, seconds || 0);
  const minutes = Math.floor(safe / 60);
  const remaining = Math.floor(safe % 60);
  return `${minutes}:${String(remaining).padStart(2, "0")}`;
}

function setRangeFill(input) {
  const min = Number(input.min) || 0;
  const max = Number(input.max) || 100;
  const value = Number(input.value) || 0;
  input.style.setProperty("--fill", `${((value - min) / (max - min)) * 100}%`);
}

function updateDerivedMetrics() {
  const density = 1 / state.settings.emissionIntervalSeconds;
  const overlap = state.settings.grainLengthSeconds / state.settings.emissionIntervalSeconds;
  dom.metricDensity.textContent = `${density.toFixed(1)} Hz`;
  dom.metricOverlap.textContent = `${overlap.toFixed(2)}×`;
  dom.densityState.textContent = `${density.toFixed(1)} /s`;
  dom.overlapValue.textContent = `${overlap.toFixed(2)}×`;
  dom.triggersValue.textContent = density.toFixed(1);
  dom.maxVoicesValue.textContent = String(state.settings.maxVoices);
  dom.metricActive.textContent = `${state.status?.activeCount || 0} / ${state.settings.maxVoices}`;
}

function updateFreezeUI() {
  dom.freezeBtn.classList.toggle("active", state.frozen);
  dom.freezeBtn.setAttribute("aria-pressed", String(state.frozen));
  dom.freezeState.textContent = state.frozen ? "frozen" : "live";
  dom.freezeState.classList.toggle("frozen", state.frozen);
  dom.freezeTag.textContent = state.frozen ? "frozen" : "scanning";
  dom.freezeTag.classList.toggle("frozen", state.frozen);
  dom.positionRange.disabled = false;
  updatePositionUI(state.position);
}

function updatePositionUI(seconds, force = false) {
  const safe = Math.max(0, Math.min(state.duration, seconds || 0));
  dom.positionValue.textContent = formatSeconds(safe);
  dom.positionReadout.textContent = `read ${safe.toFixed(3)} s`;
  if (force || performance.now() - state.lastSeekAt > 180) {
    dom.positionRange.value = String(
      state.duration > 0 ? Math.round(safe / state.duration * 1000) : 0
    );
    setRangeFill(dom.positionRange);
  }
  waveformView.setPosition(safe, state.frozen);
}

function updateHeaderState() {
  const modeName = audio.mode === "worklet"
    ? "worklet"
    : audio.mode === "script" ? "compat" : "engine";
  dom.engineBadge.classList.toggle("live", state.playing);
  dom.engineMode.textContent = state.playing ? `${modeName} live` : `${modeName} ready`;
  dom.playBtn.classList.toggle("playing", state.playing);
  dom.playLabel.textContent = state.playing ? "Playing" : "Start";
  dom.poolStatusWrap.classList.toggle("live", state.playing);
}

function handleEngineStatus(status) {
  state.status = status;
  state.statusAt = performance.now();
  state.position = status.readPositionSeconds;
  state.frozen = status.frozen;
  state.playing = status.playing;
  state.settings.maxVoices = status.maxVoices;
  state.settings.grainLengthSeconds = status.grainLengthSeconds;
  state.settings.emissionIntervalSeconds = status.emissionIntervalSeconds;
  grainView.update(status);
  updateHeaderState();
  updateFreezeUI();
  updatePositionUI(status.readPositionSeconds);
  updateLiveStatusUI(status);

  if (status.rejected > statusRejected) {
    state.rejectedFlashUntil = performance.now() + 320;
    $(".metric-alert").classList.add("rejecting");
    window.setTimeout(() => $(".metric-alert").classList.remove("rejecting"), 340);
  }
  statusRejected = status.rejected;
}

function updateLiveStatusUI(status) {
  const active = status?.activeCount || 0;
  const rejected = status?.rejected || 0;
  dom.metricActive.textContent = `${active} / ${status?.maxVoices || state.settings.maxVoices}`;
  dom.metricRejected.textContent = String(rejected);
  dom.policyRejected.textContent = String(rejected);
  dom.activeCountState.textContent = `${active} active`;
  dom.cloudEmpty.classList.toggle("hidden", active > 0 || state.playing);
  if (state.playing) {
    dom.poolStatus.textContent = active
      ? `${active} voices · no backlog`
      : "waiting for trigger";
  } else {
    dom.poolStatus.textContent = "pool idle";
  }
  updateDerivedMetrics();
}

function syncSettings(partial) {
  Object.assign(state.settings, partial);
  audio.configure(partial);
  updateDerivedMetrics();
  refreshExportSummary();
}

async function setSourceData({ source, sourceRate, name, preset }) {
  if (state.playing) await stopPlayback();
  state.source = Float32Array.from(source);
  state.sourceRate = sourceRate;
  state.sourceName = name;
  state.preset = preset || "";
  state.duration = state.source.length / sourceRate;
  state.position = 0;
  state.frozen = false;
  state.settings.frozen = false;
  state.settings.readPositionSeconds = 0;
  statusRejected = 0;
  waveformView.setSource(state.source, state.duration);
  dom.sourceName.textContent = name;
  dom.sourceMeta.textContent = `${state.duration.toFixed(2)} s · mono`;
  dom.sourceEndLabel.textContent = formatTimecode(state.duration);
  dom.positionRange.value = "0";
  setRangeFill(dom.positionRange);
  updateFreezeUI();
  updatePositionUI(0);
  updateLiveStatusUI(state.status || { activeCount: 0, rejected: 0, maxVoices: state.settings.maxVoices });
  $$(".preset").forEach((button) => button.classList.toggle("active", button.dataset.source === preset));
  await audio.setSource(state.source, state.sourceRate);
  audio.seek(0);
  audio.setFrozen(false);
}

async function loadBuiltInSource(kind) {
  const rate = audio.context?.sampleRate || 48000;
  if (kind === "impulse") {
    await setSourceData({
      source: makeImpulseSource(rate, 2.5),
      sourceRate: rate,
      name: "Single impulse",
      preset: "impulse"
    });
  } else {
    await setSourceData({
      source: makeConstantToneSource(rate, 2.5, 220),
      sourceRate: rate,
      name: "Constant tone",
      preset: "tone"
    });
  }
}

function estimatedPosition() {
  if (state.frozen) return state.position;
  if (!state.playing || !state.status) return state.position;
  const elapsed = (performance.now() - state.statusAt) / 1000;
  let position = state.status.readPositionSeconds + elapsed * state.settings.scanRate;
  if (state.duration > 0) {
    position = ((position % state.duration) + state.duration) % state.duration;
  }
  return position;
}

function setPosition(seconds) {
  const safe = Math.max(0, Math.min(state.duration, seconds));
  state.position = safe;
  state.settings.readPositionSeconds = safe;
  state.lastSeekAt = performance.now();
  audio.seek(safe);
  updatePositionUI(safe, true);
  if (state.status) {
    state.status.readPositionSeconds = safe;
    grainView.update(state.status);
  }
}

function toggleFreeze() {
  const next = !state.frozen;
  if (next) state.position = estimatedPosition();
  state.frozen = next;
  state.settings.frozen = next;
  state.settings.readPositionSeconds = state.position;
  audio.setFrozen(next);
  if (!next) audio.seek(state.position);
  if (state.status) {
    state.status.frozen = next;
    state.status.readPositionSeconds = state.position;
    grainView.update(state.status);
  }
  updateFreezeUI();
  updatePositionUI(state.position);
  showToast(next ? "Read position frozen" : "Read head scanning");
}

async function startPlayback() {
  if (!state.source.length) {
    showToast("Load an audio source first.", true);
    return;
  }
  try {
    state.settings.readPositionSeconds = state.position;
    state.settings.frozen = state.frozen;
    const mode = await audio.play();
    state.playing = true;
    updateHeaderState();
    updateLiveStatusUI(state.status || {
      activeCount: 0,
      rejected: 0,
      maxVoices: state.settings.maxVoices
    });
    if (mode === "script") showToast("Compatibility audio mode active.");
  } catch (error) {
    state.playing = false;
    updateHeaderState();
    showToast(`Audio failed: ${error.message}`, true);
  }
}

async function stopPlayback() {
  audio.stop();
  state.playing = false;
  const oldStatus = state.status;
  state.status = oldStatus
    ? { ...oldStatus, playing: false, activeCount: 0, grains: [], queueLength: 0 }
    : null;
  if (state.status) grainView.update(state.status);
  updateHeaderState();
  updateLiveStatusUI(state.status || {
    activeCount: 0,
    rejected: 0,
    maxVoices: state.settings.maxVoices
  });
}

function loadAudioFile(file) {
  if (!file) return;
  return (async () => {
    try {
      dom.loadBtn.disabled = true;
      dom.loadBtn.textContent = "Decoding…";
      await audio.ensureContext();
      const data = await file.arrayBuffer();
      const decoded = await audio.context.decodeAudioData(data);
      const mono = downmixToMono(decoded);
      const source = resampleLinear(mono, decoded.sampleRate, audio.sampleRate);
      const cleanName = file.name.replace(/\.[^.]+$/, "") || "Loaded audio";
      await setSourceData({
        source,
        sourceRate: audio.sampleRate,
        name: cleanName,
        preset: ""
      });
      showToast(`Loaded ${file.name} · ${state.duration.toFixed(2)} s`);
    } catch (error) {
      showToast(`Could not decode audio: ${error.message}`, true);
    } finally {
      dom.loadBtn.disabled = false;
      dom.loadBtn.textContent = "Load audio";
      dom.fileInput.value = "";
    }
  })();
}

function bindControls() {
  dom.playBtn.addEventListener("click", () => {
    if (state.playing) stopPlayback();
    else startPlayback();
  });
  dom.stopBtn.addEventListener("click", stopPlayback);
  dom.freezeBtn.addEventListener("click", toggleFreeze);
  dom.loadBtn.addEventListener("click", () => dom.fileInput.click());
  dom.fileInput.addEventListener("change", () => loadAudioFile(dom.fileInput.files[0]));
  dom.runDiagnostics.addEventListener("click", () => runDiagnostics(true));

  dom.grainLength.addEventListener("input", () => {
    const ms = Number(dom.grainLength.value);
    dom.grainLengthValue.textContent = `${ms} ms`;
    syncSettings({ grainLengthSeconds: ms / 1000 });
    setRangeFill(dom.grainLength);
  });

  dom.emissionInterval.addEventListener("input", () => {
    const ms = Number(dom.emissionInterval.value);
    dom.emissionIntervalValue.textContent = `${ms} ms`;
    syncSettings({ emissionIntervalSeconds: ms / 1000 });
    setRangeFill(dom.emissionInterval);
  });

  dom.maxVoices.addEventListener("input", () => {
    const voices = Number(dom.maxVoices.value);
    dom.maxVoicesValue.textContent = String(voices);
    syncSettings({ maxVoices: voices });
    setRangeFill(dom.maxVoices);
  });

  dom.masterGain.addEventListener("input", () => {
    const gain = Number(dom.masterGain.value);
    dom.masterGainValue.textContent = gain <= 0.0001
      ? "−∞ dB"
      : `${(20 * Math.log10(gain)).toFixed(1)} dB`;
    syncSettings({ masterGain: gain });
    setRangeFill(dom.masterGain);
  });

  dom.scanRate.addEventListener("input", () => {
    const rate = Number(dom.scanRate.value);
    dom.scanRateValue.textContent = `${rate.toFixed(2)}×`;
    syncSettings({ scanRate: rate });
    setRangeFill(dom.scanRate);
  });

  dom.positionRange.addEventListener("input", () => {
    const ratio = Number(dom.positionRange.value) / 1000;
    setPosition(ratio * state.duration);
    setRangeFill(dom.positionRange);
  });

  dom.windowButtons.addEventListener("click", (event) => {
    const button = event.target.closest("[data-window]");
    if (!button) return;
    $$(".window-button").forEach((item) => item.classList.toggle("active", item === button));
    syncSettings({ windowType: button.dataset.window });
  });

  $$(".preset").forEach((button) => {
    button.addEventListener("click", () => loadBuiltInSource(button.dataset.source));
  });

  let draggingWave = false;
  const seekFromPointer = (event) => {
    const ratio = waveformView.positionFromPointer(event);
    setPosition(ratio * state.duration);
  };
  $("#waveCanvas").addEventListener("pointerdown", (event) => {
    draggingWave = true;
    event.currentTarget.setPointerCapture(event.pointerId);
    seekFromPointer(event);
  });
  $("#waveCanvas").addEventListener("pointermove", (event) => {
    if (draggingWave) seekFromPointer(event);
  });
  $("#waveCanvas").addEventListener("pointerup", () => { draggingWave = false; });
  $("#waveCanvas").addEventListener("pointercancel", () => { draggingWave = false; });
}

async function runDiagnostics(notify = true) {
  dom.runDiagnostics.disabled = true;
  const cards = [...dom.diagnosticsGrid.children];
  cards.forEach((card) => {
    card.className = "diagnostic running";
    card.querySelector("small").textContent = "running…";
  });
  await new Promise((resolve) => window.setTimeout(resolve, 30));
  try {
    const results = runAllDiagnostics(audio.sampleRate);
    results.forEach((result, index) => {
      const card = cards[index];
      if (!card) return;
      card.className = `diagnostic ${result.passed ? "pass" : "fail"}`;
      card.querySelector("p").textContent = result.label;
      card.querySelector("small").textContent = result.passed ? "pass" : "failed";
      card.title = result.detail;
    });
    const allPassed = results.every((result) => result.passed);
    if (notify) {
      showToast(
        allPassed
          ? "DSP checks passed: onset, overlap, tone, voice cap, freeze."
          : "One or more DSP checks failed.",
        !allPassed
      );
    }
  } catch (error) {
    showToast(`Diagnostics failed: ${error.message}`, true);
  } finally {
    dom.runDiagnostics.disabled = false;
  }
}

function refreshExportSummary() {
  if (!dom.exportSummary) return;
  const lengthMs = Math.round(state.settings.grainLengthSeconds * 1000);
  const intervalMs = Math.round(state.settings.emissionIntervalSeconds * 1000);
  const overlap = state.settings.grainLengthSeconds / state.settings.emissionIntervalSeconds;
  dom.exportSummary.innerHTML = [
    `<div><span>Grain / interval</span><strong>${lengthMs} / ${intervalMs} ms</strong></div>`,
    `<div><span>Overlap / cap</span><strong>${overlap.toFixed(2)}× / ${state.settings.maxVoices}</strong></div>`,
    `<div><span>Window / policy</span><strong>${state.settings.windowType} / reject-new</strong></div>`
  ].join("");
}

function openExportDialog() {
  refreshExportSummary();
  dom.exportStatus.textContent = "Ready";
  dom.exportPercent.textContent = "0%";
  dom.exportProgressBar.style.width = "0%";
  dom.exportDurationValue.textContent = `${Number(dom.exportDuration.value).toFixed(1)} s`;
  if (typeof dom.exportDialog.showModal === "function") dom.exportDialog.showModal();
  else dom.exportDialog.setAttribute("open", "");
}

function closeExportDialog() {
  if (state.exporting) return;
  if (typeof dom.exportDialog.close === "function") dom.exportDialog.close();
  else dom.exportDialog.removeAttribute("open");
}

async function exportPcm() {
  if (state.exporting || !state.source.length) return;
  state.exporting = true;
  dom.confirmExport.disabled = true;
  dom.confirmExport.textContent = "Rendering…";
  dom.exportStatus.textContent = "Preparing source";
  try {
    const sampleRate = audio.sampleRate;
    const source = resampleLinear(state.source, state.sourceRate, sampleRate);
    const duration = Number(dom.exportDuration.value);
    const readPosition = state.frozen ? state.position : estimatedPosition();
    const settings = {
      ...state.settings,
      readPositionSeconds: readPosition,
      frozen: state.frozen
    };
    const result = await renderGranularPcm(
      { source, sampleRate, durationSeconds: duration, settings },
      (progress) => {
        const percent = Math.round(progress * 100);
        dom.exportProgressBar.style.width = `${percent}%`;
        dom.exportPercent.textContent = `${percent}%`;
        dom.exportStatus.textContent = "Synthesizing grains";
      }
    );
    dom.exportStatus.textContent = "Encoding 16-bit WAV";
    const wav = pcmToStereoWav(result.pcm, sampleRate);
    const blob = new Blob([wav], { type: "audio/wav" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    link.href = url;
    link.download = `grain-field-${Math.round(result.status.grainLengthSeconds * 1000)}ms-${stamp}.wav`;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1500);

    dom.exportProgressBar.style.width = "100%";
    dom.exportPercent.textContent = "100%";
    dom.exportStatus.textContent = `Done · ${result.status.accepted} grains · ${result.status.rejected} rejected`;
    showToast(`Exported ${duration.toFixed(1)} s of synthesized PCM.`);
  } catch (error) {
    dom.exportStatus.textContent = "Export failed";
    showToast(`Export failed: ${error.message}`, true);
  } finally {
    state.exporting = false;
    dom.confirmExport.disabled = false;
    dom.confirmExport.textContent = "Render & download";
  }
}

function bindExport() {
  [dom.exportBtn, dom.exportWideBtn].forEach((button) => {
    button.addEventListener("click", openExportDialog);
  });
  dom.closeExport.addEventListener("click", (event) => {
    if (state.exporting) event.preventDefault();
  });
  dom.cancelExport.addEventListener("click", closeExportDialog);
  dom.exportDuration.addEventListener("input", () => {
    dom.exportDurationValue.textContent = `${Number(dom.exportDuration.value).toFixed(1)} s`;
    setRangeFill(dom.exportDuration);
  });
  dom.confirmExport.addEventListener("click", exportPcm);
}

function refreshGainLabel() {
  const gain = Number(dom.masterGain.value);
  dom.masterGainValue.textContent = gain <= 0.0001
    ? "−∞ dB"
    : `${(20 * Math.log10(gain)).toFixed(1)} dB`;
}

function animationLoop() {
  const position = estimatedPosition();
  waveformView.setPosition(position, state.frozen);
  waveformView.draw();
  grainView.draw(audio.sampleRate);
  window.requestAnimationFrame(animationLoop);
}

async function initialize() {
  bindControls();
  bindExport();
  [dom.positionRange, dom.scanRate, dom.grainLength, dom.emissionInterval, dom.maxVoices,
    dom.masterGain, dom.exportDuration].forEach(setRangeFill);
  refreshGainLabel();
  refreshExportSummary();
  updateDerivedMetrics();
  updateHeaderState();
  updateLiveStatusUI({ activeCount: 0, rejected: 0, maxVoices: state.settings.maxVoices });
  audio.configure(state.settings);
  await loadBuiltInSource("tone");
  window.requestAnimationFrame(animationLoop);
  window.setTimeout(() => runDiagnostics(false), 450);
}

initialize().catch((error) => {
  showToast(`Initialization failed: ${error.message}`, true);
});
