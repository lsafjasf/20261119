import { runMeasurementSuite } from "./measurement.js";
import {
  drawEmptyChart,
  drawImpulseChart,
  drawJumpChart,
  drawMap,
  drawScope,
  drawSweepChart,
} from "./visuals.js";

const $ = (id) => document.getElementById(id);
const state = {
  audio: null,
  node: null,
  running: false,
  paused: false,
  delayMs: 23.4,
  feedback: 0.35,
  mix: 1,
  interpolation: "cubic",
  crossfadeMs: 28,
  source: "sine",
  telemetry: null,
  tests: {},
  testRunning: false,
  jumping: false,
};

const sourceCopy = {
  sine: "正弦 · 220 Hz · −11 dBFS",
  sweep: "对数扫频 · 30 Hz → 18 kHz · 7 s",
  impulse: "冲激串 · 每 720 ms · 单样本",
  noise: "确定性噪声 · −18 dBFS",
};

const interpolationCopy = {
  linear: "两点线性核，结构简单且无过冲；分数相位越接近半样本，高频衰减越明显。",
  cubic: "四点 Lagrange 核，用相邻样本重建分数位置；高频响应更平，但会出现轻微过冲。",
};

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function setRangeFill(input) {
  const minimum = Number(input.min);
  const maximum = Number(input.max);
  const fill = ((Number(input.value) - minimum) / (maximum - minimum)) * 100;
  input.style.setProperty("--fill", `${fill}%`);
}

function send(message) {
  if (state.node) state.node.port.postMessage(message);
}

function setEnginePill(mode, text) {
  $("enginePill").dataset.state = mode;
  $("enginePillText").textContent = text;
}

function updateDelayUI(value, { notify = false } = {}) {
  const delay = clamp(Number(value) || 0.5, 0.5, 480);
  state.delayMs = delay;
  $("delayRange").value = String(delay);
  $("delayNumber").value = delay.toFixed(1);
  setRangeFill($("delayRange"));
  const samples = (delay / 1000) * 48000;
  const fraction = samples % 1;
  $("fractionReadout").textContent = `${fraction.toFixed(3)} samples`;
  $("fractionFill").style.width = `${fraction * 100}%`;
  for (const button of document.querySelectorAll("[data-delay]")) {
    button.classList.toggle("active", Math.abs(Number(button.dataset.delay) - delay) < 0.05);
  }
  if (!state.telemetry) $("targetDelay").textContent = `${delay.toFixed(2)} ms`;
  if (notify) send({ type: "delay", value: delay });
}

function updateFeedbackUI(value) {
  const feedback = clamp(Number(value) || 0, 0, 0.995);
  state.feedback = feedback;
  $("feedbackRange").value = String(feedback);
  $("feedbackValue").textContent = feedback.toFixed(3);
  $("safetyFill").style.width = `${(feedback / 0.995) * 100}%`;
  setRangeFill($("feedbackRange"));
  const hot = feedback >= 0.95;
  $("safetyBadge").dataset.state = hot ? "hot" : "stable";
  $("safetyBadge").textContent = hot ? "LIMITED" : "STABLE";
}

function updateMixUI(value) {
  const mix = clamp(Number(value), 0, 100);
  state.mix = mix / 100;
  $("mixRange").value = String(mix);
  $("mixValue").textContent = `${mix}%`;
  setRangeFill($("mixRange"));
}

function updateCrossfadeUI(value) {
  state.crossfadeMs = clamp(Number(value) || 2, 2, 120);
  $("crossfadeRange").value = String(state.crossfadeMs);
  $("crossfadeValue").textContent = `${state.crossfadeMs.toFixed(0)} ms`;
  setRangeFill($("crossfadeRange"));
}

function updateInterpolationUI(mode, { notify = false } = {}) {
  state.interpolation = mode === "linear" ? "linear" : "cubic";
  for (const button of document.querySelectorAll("[data-interp]")) {
    button.classList.toggle("active", button.dataset.interp === state.interpolation);
  }
  $("kernelBadge").textContent = state.interpolation.toUpperCase();
  $("interpCopy").textContent = interpolationCopy[state.interpolation];
  if (notify) send({ type: "interpolation", interpolation: state.interpolation });
}

function updateSourceUI(mode, { notify = false } = {}) {
  state.source = mode;
  for (const button of document.querySelectorAll("[data-source]")) {
    button.classList.toggle("active", button.dataset.source === mode);
  }
  $("sourceNote").textContent = sourceCopy[mode];
  if (notify) send({ type: "source", source: mode });
}

function updatePauseUI() {
  $("pauseLabel").textContent = state.paused ? "恢复" : "暂停";
  $("pauseBtn").classList.toggle("active", state.paused);
  $("scopePaused").classList.toggle("visible", state.paused);
  if (state.running) {
    if (state.paused) {
      setEnginePill("paused", "ENGINE PAUSED");
      $("engineStatusText").textContent = "缓冲已冻结";
    } else {
      setEnginePill("running", "ENGINE LIVE");
      $("engineStatusText").textContent = "实时处理 / AudioWorklet";
    }
  }
}

function formatDb(value) {
  if (!Number.isFinite(value) || value <= 0) return "−∞";
  const db = 20 * Math.log10(value);
  return db <= -60 ? "−∞" : db.toFixed(1);
}

function applyTelemetry(message) {
  state.telemetry = message;
  const sampleRate = message.sampleRate || 48000;
  const effectiveMs = (message.effectiveDelay / sampleRate) * 1000;
  const targetMs = (message.targetDelay / sampleRate) * 1000;
  const gainA = Math.round((message.gainA ?? 1) * 100);
  const gainB = 100 - gainA;
  $("effectiveDelay").textContent = effectiveMs.toFixed(2);
  $("targetDelay").textContent = `${targetMs.toFixed(2)} ms`;
  $("writePos").textContent = Math.floor(message.writePosition).toLocaleString("en-US");
  $("readPosA").textContent = Math.floor(message.readPositionA).toLocaleString("en-US");
  $("readPosB").textContent = Math.floor(message.readPositionB).toLocaleString("en-US");
  $("gainA").textContent = `${gainA}%`;
  $("gainB").textContent = `${gainB}%`;
  $("xfadeFill").style.width = `${gainB}%`;
  $("xfadeStatus").textContent = message.fading ? "CROSSFADING" : "STABLE / IDLE";
  $("inPeak").textContent = formatDb(message.inputPeak);
  $("outPeak").textContent = formatDb(message.outputPeak);
  $("blockSerial").textContent = String(message.blockSerial).padStart(6, "0");

  const incomingName = message.incoming === 0 ? "A" : "B";
  $("headState").dataset.state = message.fading ? "fading" : "idle";
  $("headStateText").textContent = message.fading
    ? `HEAD ${incomingName} IN`
    : `HEAD ${message.activeIndex === 0 ? "A" : "B"}`;
  $("fractionReadout").textContent = `${(message.effectiveDelay % 1).toFixed(3)} samples`;
  $("fractionFill").style.width = `${(message.effectiveDelay % 1) * 100}%`;

  if (message.paused !== state.paused) {
    state.paused = message.paused;
    updatePauseUI();
  }
}

function reportAudioError(error) {
  console.error(error);
  setEnginePill("error", "ENGINE ERROR");
  $("engineStatusText").textContent = "AudioWorklet 启动失败";
  $("powerLabel").textContent = "重试引擎";
  state.running = false;
}

async function toggleEngine() {
  if (state.audio) {
    try {
      if (state.audio.state === "running") {
        await state.audio.suspend();
        state.running = false;
        setEnginePill("idle", "ENGINE SUSPENDED");
        $("engineStatusText").textContent = "音频线程已挂起";
        $("powerLabel").textContent = "恢复引擎";
        $("powerBtn").classList.remove("running");
      } else {
        await state.audio.resume();
        state.running = true;
        updatePauseUI();
        $("engineStatusText").textContent = "实时处理 / AudioWorklet";
        $("powerLabel").textContent = "挂起引擎";
        $("powerBtn").classList.add("running");
      }
    } catch (error) {
      reportAudioError(error);
    }
    return;
  }

  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass || !("audioWorklet" in AudioContextClass.prototype)) {
    reportAudioError(new Error("当前浏览器不支持 AudioWorklet"));
    return;
  }

  let audio;
  try {
    audio = new AudioContextClass({ sampleRate: 48000, latencyHint: "interactive" });
    const workletUrl = new URL("../worklet/delay-worklet.js", import.meta.url).href;
    await audio.audioWorklet.addModule(workletUrl);
    const node = new AudioWorkletNode(audio, "fractional-delay", {
      numberOfInputs: 0,
      numberOfOutputs: 1,
      outputChannelCount: [2],
      processorOptions: {
        delayMs: state.delayMs,
        feedback: state.feedback,
        mix: state.mix,
        interpolation: state.interpolation,
        crossfadeMs: state.crossfadeMs,
        source: state.source,
      },
    });
    node.port.onmessage = (event) => {
      if (event.data?.type === "telemetry") applyTelemetry(event.data);
    };
    node.connect(audio.destination);
    audio.onstatechange = () => {
      if (audio.state === "running" && state.running) updatePauseUI();
    };
    state.audio = audio;
    state.node = node;
    await audio.resume();
    state.running = true;
    state.paused = false;
    $("powerBtn").classList.add("running");
    $("powerLabel").textContent = "挂起引擎";
    $("pauseBtn").disabled = false;
    $("jumpBtn").disabled = false;
    $("flushBtn").disabled = false;
    setEnginePill("running", "ENGINE LIVE");
    $("engineStatusText").textContent = `实时处理 / ${Math.round(audio.sampleRate / 100) / 10} kHz`;
  } catch (error) {
    if (audio) await audio.close().catch(() => {});
    state.audio = null;
    state.node = null;
    reportAudioError(error);
  }
}

function togglePause() {
  if (!state.node || !state.running) return;
  state.paused = !state.paused;
  send({ type: "pause", paused: state.paused });
  updatePauseUI();
  if (state.paused) {
    $("engineStatusText").textContent = "暂停 · 等待恢复";
  }
}

function triggerParameterJump() {
  if (state.jumping) return;
  state.jumping = true;
  $("jumpBtn").disabled = true;
  const sequence = [5.2, 46.8, 11.7, 32.2, 7.4, 23.4];
  let index = 0;
  const step = () => {
    updateDelayUI(sequence[index], { notify: true });
    index += 1;
    if (index < sequence.length) {
      setTimeout(step, 155);
    } else {
      state.jumping = false;
      $("jumpBtn").disabled = false;
    }
  };
  step();
}

function bindControls() {
  $("powerBtn").addEventListener("click", () => {
    toggleEngine();
  });
  $("pauseBtn").addEventListener("click", togglePause);
  $("jumpBtn").addEventListener("click", triggerParameterJump);
  $("flushBtn").addEventListener("click", () => send({ type: "flush" }));

  $("delayRange").addEventListener("input", (event) => {
    updateDelayUI(event.target.value, { notify: true });
  });
  $("delayNumber").addEventListener("change", (event) => {
    updateDelayUI(event.target.value, { notify: true });
  });
  for (const button of document.querySelectorAll("[data-delay]")) {
    button.addEventListener("click", () => {
      updateDelayUI(button.dataset.delay, { notify: true });
    });
  }

  $("feedbackRange").addEventListener("input", (event) => {
    updateFeedbackUI(event.target.value);
    send({ type: "feedback", value: state.feedback });
  });
  $("mixRange").addEventListener("input", (event) => {
    updateMixUI(event.target.value);
    send({ type: "mix", value: state.mix });
  });
  $("crossfadeRange").addEventListener("input", (event) => {
    updateCrossfadeUI(event.target.value);
    send({ type: "crossfade", value: state.crossfadeMs });
  });

  for (const button of document.querySelectorAll("[data-interp]")) {
    button.addEventListener("click", () => {
      updateInterpolationUI(button.dataset.interp, { notify: true });
    });
  }
  for (const button of document.querySelectorAll("[data-source]")) {
    button.addEventListener("click", () => {
      updateSourceUI(button.dataset.source, { notify: true });
    });
  }

  window.addEventListener("keydown", (event) => {
    if (event.code !== "Space") return;
    const tag = document.activeElement?.tagName;
    if (tag === "INPUT" || tag === "BUTTON" || tag === "TEXTAREA") return;
    event.preventDefault();
    togglePause();
  });
}

function updateMetricRows(testName, rows) {
  const table = $(`${testName}Metrics`);
  const elements = [...table.querySelectorAll(":scope > div:not(.metric-table-head)")];
  rows.forEach((row, index) => {
    const cells = elements[index]?.querySelectorAll("b");
    if (!cells) return;
    cells[0].textContent = row[0] ?? "—";
    cells[1].textContent = row[1] ?? "—";
    cells[0].className = row[2] || "";
    cells[1].className = row[3] || "";
  });
}

function setTestState(testName, stateName, label) {
  const element = $(`${testName}State`);
  element.className = `test-state ${stateName}`;
  element.textContent = label;
}

function renderImpulse(data) {
  const linear = data.linear.metrics;
  const cubic = data.cubic.metrics;
  const errorClass = (error) => (Math.abs(error) < 0.01 ? "good" : "bad");
  updateMetricRows("impulse", [
    [`${linear.targetMs.toFixed(3)} ms`, `${cubic.targetMs.toFixed(3)} ms`],
    [`${linear.measuredMs.toFixed(4)} ms`, `${cubic.measuredMs.toFixed(4)} ms`],
    [
      `${linear.errorSamples >= 0 ? "+" : ""}${linear.errorSamples.toFixed(5)} smp`,
      `${cubic.errorSamples >= 0 ? "+" : ""}${cubic.errorSamples.toFixed(5)} smp`,
      errorClass(linear.errorSamples),
      errorClass(cubic.errorSamples),
    ],
    [`${linear.fraction.toFixed(4)}`, `${cubic.fraction.toFixed(4)}`],
  ]);
  drawImpulseChart($("impulseChart"), data);
  setTestState("impulse", "done", "PASS");
}

function renderSweep(data) {
  const linear = data.linear.metrics;
  const cubic = data.cubic.metrics;
  updateMetricRows("sweep", [
    [`${linear.averageDeviationDb.toFixed(3)} dB`, `${cubic.averageDeviationDb.toFixed(3)} dB`],
    [`${linear.maxDeviationDb.toFixed(3)} dB`, `${cubic.maxDeviationDb.toFixed(3)} dB`],
    [`${linear.db10k.toFixed(3)} dB`, `${cubic.db10k.toFixed(3)} dB`],
    [`${linear.db18k.toFixed(3)} dB`, `${cubic.db18k.toFixed(3)} dB`],
  ]);
  drawSweepChart($("sweepChart"), data);
  setTestState("sweep", "done", "MEASURED");
}

function renderJump(data) {
  const linear = data.linear.metrics;
  const cubic = data.cubic.metrics;
  const stable = (metrics) => (metrics.stable ? "good" : "bad");
  updateMetricRows("jump", [
    [`${linear.maxDelta.toFixed(3)} ×`, `${cubic.maxDelta.toFixed(3)} ×`],
    [`${linear.peakGain.toFixed(3)} ×`, `${cubic.peakGain.toFixed(3)} ×`],
    [
      linear.stable ? "PASS" : "FAIL",
      cubic.stable ? "PASS" : "FAIL",
      stable(linear),
      stable(cubic),
    ],
    [`${linear.nonFinite}`, `${cubic.nonFinite}`, stable(linear), stable(cubic)],
  ]);
  drawJumpChart($("jumpChart"), data);
  setTestState("jump", linear.stable && cubic.stable ? "done" : "error", "PASS");
}

function drawTestPlaceholders(label = "QUEUED") {
  drawEmptyChart($("impulseChart"), label);
  drawEmptyChart($("sweepChart"), label);
  drawEmptyChart($("jumpChart"), label);
}

async function runMeasurements() {
  if (state.testRunning) return;
  state.testRunning = true;
  state.tests = {};
  const button = $("runTestsBtn");
  button.classList.add("running");
  button.disabled = true;
  for (const name of ["impulse", "sweep", "jump"]) {
    setTestState(name, "running", "RUNNING");
    $(`${name}State`).textContent = "RUNNING";
  }
  drawTestPlaceholders("ANALYZING…");
  $("measureTimestamp").textContent = "正在生成测试信号";
  const started = performance.now();

  try {
    const results = await runMeasurementSuite((name, data) => {
      if (name === "impulse") renderImpulse(data);
      if (name === "sweep") renderSweep(data);
      if (name === "jump") renderJump(data);
    });
    state.tests = results;
    const elapsed = ((performance.now() - started) / 1000).toFixed(2);
    const time = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    $("measureTimestamp").textContent = `${time} · ${elapsed} s · 48 kHz`;
  } catch (error) {
    console.error(error);
    $("measureTimestamp").textContent = "测量失败";
    for (const name of ["impulse", "sweep", "jump"]) {
      setTestState(name, "error", "ERROR");
    }
  } finally {
    state.testRunning = false;
    button.classList.remove("running");
    button.disabled = false;
  }
}

function redrawCharts() {
  if (state.tests.impulse) drawImpulseChart($("impulseChart"), state.tests.impulse);
  if (state.tests.sweep) drawSweepChart($("sweepChart"), state.tests.sweep);
  if (state.tests.jump) drawJumpChart($("jumpChart"), state.tests.jump);
}

function animate() {
  drawScope($("scopeCanvas"), state.telemetry);
  drawMap($("mapCanvas"), state.telemetry);
  requestAnimationFrame(animate);
}

function initialize() {
  updateDelayUI(23.4);
  updateFeedbackUI(0.35);
  updateMixUI(100);
  updateCrossfadeUI(28);
  updateInterpolationUI("cubic");
  updateSourceUI("sine");
  drawTestPlaceholders();
  bindControls();
  $("runTestsBtn").addEventListener("click", runMeasurements);

  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(redrawCharts, 140);
  });

  animate();
  window.setTimeout(runMeasurements, 260);
}

initialize();
