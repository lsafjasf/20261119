(function () {
  "use strict";

  const Core = window.SynthCore;
  const $ = (selector, scope) => (scope || document).querySelector(selector);
  const $$ = (selector, scope) => Array.from((scope || document).querySelectorAll(selector));
  const clamp = Core.clamp;

  const SAMPLE_RATE = 48000;
  const AUDIT_TARGETS = [40, 57, 64, 69];
  const NOTE_NAMES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"];

  const state = {
    midi: 57,
    frequency: Core.midiToFrequency(57),
    position: 0.21,
    damping: 0.46,
    hardness: 0.58,
    velocity: 0.84,
    notchA: 0.2,
    notchB: 0.33,
    currentRender: null,
    currentPlayStart: 0,
    currentPlayEnd: 0,
    audioContext: null,
    masterGain: null,
    activeSources: new Set(),
    muted: false,
    events: [],
    isDemo: false,
    recordOrigin: 0,
    recordingActive: false,
    replaying: false,
    replayStartedAt: 0,
    replayDuration: 0,
    replayTimers: [],
    replayAnimation: 0,
    toastTimer: 0,
    notchTimer: 0,
    auditRunning: false,
    auditTimer: 0,
    animationStarted: false
  };

  function noteName(midi) {
    const value = Math.round(midi);
    return NOTE_NAMES[((value % 12) + 12) % 12] + (Math.floor(value / 12) - 1);
  }

  function frequencyForMidi(midi) {
    return 440 * Math.pow(2, (midi - 69) / 12);
  }

  function formatFrequency(frequency) {
    return frequency >= 100 ? frequency.toFixed(2) : frequency.toFixed(3);
  }

  function formatTime(seconds) {
    const safe = Math.max(0, seconds);
    const minutes = Math.floor(safe / 60);
    const remainder = safe - minutes * 60;
    return String(minutes).padStart(2, "0") + ":" + remainder.toFixed(3).padStart(6, "0");
  }

  function secondsToLabel(seconds) {
    return seconds < 10 ? seconds.toFixed(2) : seconds.toFixed(1);
  }

  function signed(value, digits) {
    const precision = digits == null ? 2 : digits;
    const prefix = value > 0 ? "+" : value < 0 ? "−" : "±";
    return prefix + Math.abs(value).toFixed(precision);
  }

  function debounce(callback, delay) {
    let timer = 0;
    return function debounced() {
      const args = arguments;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => callback.apply(null, args), delay);
    };
  }

  function showToast(message) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.classList.add("is-visible");
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 2600);
  }

  function downloadBlob(blob, filename) {
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function downloadWav(samples, filename) {
    const bytes = Core.encodeWav(samples, SAMPLE_RATE);
    downloadBlob(new Blob([bytes], { type: "audio/wav" }), filename);
  }

  function makeSeed() {
    return ((Date.now() & 0x7fffffff) ^ Math.floor(Math.random() * 0x7fffffff)) >>> 0;
  }

  function createDemoEvents() {
    return [
      {
        id: "demo-1",
        type: "pluck",
        time: 0,
        midi: 57,
        frequency: 220,
        position: 0.21,
        damping: 0.38,
        hardness: 0.62,
        velocity: 0.86,
        seed: 0x2a11
      },
      {
        id: "demo-2",
        type: "pluck",
        time: 0.72,
        midi: 64,
        frequency: Core.midiToFrequency(64),
        position: 0.28,
        damping: 0.34,
        hardness: 0.7,
        velocity: 0.72,
        seed: 0x2a12
      },
      {
        id: "demo-3",
        type: "pluck",
        time: 1.42,
        midi: 69,
        frequency: 440,
        position: 0.33,
        damping: 0.54,
        hardness: 0.55,
        velocity: 0.8,
        seed: 0x2a13
      },
      {
        id: "demo-4",
        type: "pluck",
        time: 2.08,
        midi: 57,
        frequency: 220,
        position: 0.2,
        damping: 0.7,
        hardness: 0.78,
        velocity: 0.9,
        seed: 0x2a14
      }
    ];
  }

  function eventTimeNow() {
    if (!state.recordingActive) {
      state.recordingActive = true;
      state.recordOrigin = performance.now();
      return 0;
    }
    return Math.max(0, (performance.now() - state.recordOrigin) / 1000);
  }

  function newId(prefix) {
    return (prefix || "event") + "-" + Date.now().toString(36) + "-" + Math.floor(Math.random() * 9999).toString(36);
  }

  function beginUserRecording() {
    if (!state.isDemo) return;
    state.events = [];
    state.isDemo = false;
    state.recordingActive = false;
    state.recordOrigin = performance.now();
  }

  let audio;
  function ensureAudio() {
    if (state.audioContext) {
      if (state.audioContext.state === "suspended") state.audioContext.resume();
      return state.audioContext;
    }
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return null;
    state.audioContext = new AudioContextClass();
    state.masterGain = state.audioContext.createGain();
    state.masterGain.gain.value = state.muted ? 0 : 0.78;
    const limiter = state.audioContext.createDynamicsCompressor();
    limiter.threshold.value = -12;
    limiter.knee.value = 8;
    limiter.ratio.value = 8;
    limiter.attack.value = 0.002;
    limiter.release.value = 0.15;
    state.masterGain.connect(limiter);
    limiter.connect(state.audioContext.destination);
    return state.audioContext;
  }

  function setEngineLive(live) {
    const status = $("#engineStatus");
    status.classList.toggle("is-live", live);
    $("span", status).textContent = live ? "ENGINE LIVE" : "ENGINE IDLE";
  }

  function playRenderedNote(render) {
    const context = ensureAudio();
    if (!context) {
      showToast("Web Audio is unavailable in this browser.");
      return;
    }
    const buffer = context.createBuffer(1, render.samples.length, render.sampleRate);
    buffer.copyToChannel(render.samples, 0);
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(state.masterGain);
    const startAt = context.currentTime + 0.012;
    source.start(startAt);
    state.activeSources.add(source);
    state.currentPlayStart = performance.now() + 12;
    state.currentPlayEnd = state.currentPlayStart + render.duration * 1000;
    setEngineLive(true);
    source.onended = () => {
      state.activeSources.delete(source);
      if (!state.activeSources.size && !state.replaying) setEngineLive(false);
    };
  }

  function stopAllAudio() {
    for (const source of state.activeSources) {
      try {
        source.stop();
      } catch (error) {
        // Source may have already ended.
      }
    }
    state.activeSources.clear();
    setEngineLive(false);
  }

  function addEvent(event) {
    state.events.push(event);
    state.events.sort((left, right) => left.time - right.time);
    renderTimeline();
  }

  function recordParam(name, value) {
    if (!state.recordingActive || state.replaying) return;
    const time = eventTimeNow();
    const last = state.events[state.events.length - 1];
    if (last && last.type === "param" && last.name === name && time - last.time < 0.24) {
      last.value = value;
      last.time = time;
      renderTimeline();
      return;
    }
    addEvent({
      id: newId("param"),
      type: "param",
      time,
      name,
      value
    });
  }

  function updateSynthReadouts() {
    const loop = Core.loopParameters(state.frequency, SAMPLE_RATE, state.damping, state.hardness);
    $("#delaySamples").textContent = loop.desiredDelay.toFixed(1);
    $("#integerDelay").textContent = String(loop.integerDelay);
    $("#fractionalDelay").textContent = loop.fractionalDelay.toFixed(2);
    $("#loopGain").textContent = loop.loopGain.toFixed(5);
    $("#pathFormula").textContent = "N = " + loop.desiredDelay.toFixed(1) + " samples";
    $("#gammaMetric").textContent = loop.loopGain.toFixed(4);
    $("#tailMetric").textContent = loop.t60.toFixed(1);
    $("#dampingTime").textContent = loop.t60.toFixed(1) + " s tail";
    $("#excitationCutoff").textContent = (loop.cutoff / 1000).toFixed(1) + " kHz loss";
  }

  function setPitch(value, options) {
    const settings = options || {};
    const midi = clamp(Math.round(value), 36, 84);
    state.midi = midi;
    state.frequency = frequencyForMidi(midi);
    $("#noteName").textContent = noteName(midi);
    $("#noteFrequency").textContent = formatFrequency(state.frequency);
    $("#noteMidi").textContent = "MIDI " + midi + " · +0 cents";
    $("#pitchSlider").value = String(midi);
    $("#notchPitch").textContent = noteName(midi) + " · SAME PITCH";
    updateSynthReadouts();
    queueNotchAnalysis();
    if (settings.record) recordParam("midi", midi);
  }

  function setDamping(value, options) {
    const settings = options || {};
    state.damping = clamp(value, 0, 1);
    const percent = Math.round(state.damping * 100);
    const knob = $("#dampingKnob");
    knob.setAttribute("aria-valuenow", String(percent));
    $("#dampingValue").textContent = String(percent);
    $("#dampingArc").style.strokeDasharray = (state.damping * 184).toFixed(2) + " 245";
    $("#dampingNeedle").style.transform = "rotate(" + (state.damping * 270).toFixed(2) + "deg)";
    updateSynthReadouts();
    if (settings.record) recordParam("damping", state.damping);
    queueNotchAnalysis();
    if (settings.record) queuePitchAudit();
  }

  function setHardness(value, options) {
    const settings = options || {};
    state.hardness = clamp(value, 0, 1);
    const percent = Math.round(state.hardness * 100);
    const knob = $("#excitationKnob");
    knob.setAttribute("aria-valuenow", String(percent));
    $("#excitationValue").textContent = String(percent);
    $("#excitationArc").style.strokeDasharray = (state.hardness * 184).toFixed(2) + " 245";
    $("#excitationNeedle").style.transform = "rotate(" + (state.hardness * 270).toFixed(2) + "deg)";
    updateSynthReadouts();
    if (settings.record) recordParam("hardness", state.hardness);
    queueNotchAnalysis();
    if (settings.record) queuePitchAudit();
  }

  function setPluckPosition(value, options) {
    const settings = options || {};
    state.position = clamp(value, 0.04, 0.48);
    $("#pluckSlider").value = state.position.toFixed(2);
    $("#pluckOutput").textContent = "β " + state.position.toFixed(2);
    $("#pluckMarker").style.left = (state.position * 100).toFixed(2) + "%";
    $("#loopPosition").style.left = (9 + state.position * 82).toFixed(2) + "%";
    $("#loopPosition").querySelector("b").textContent = state.position.toFixed(2);
    if (settings.record) recordParam("position", state.position);
  }

  function setupKnob(element, getValue, setValue) {
    let dragging = false;
    let startY = 0;
    let startValue = 0;

    element.addEventListener("pointerdown", (event) => {
      dragging = true;
      startY = event.clientY;
      startValue = getValue();
      element.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    element.addEventListener("pointermove", (event) => {
      if (!dragging) return;
      setValue(clamp(startValue + (startY - event.clientY) / 145, 0, 1));
    });
    const finish = () => {
      if (!dragging) return;
      dragging = false;
      setValue(getValue(), { record: true });
    };
    element.addEventListener("pointerup", finish);
    element.addEventListener("pointercancel", finish);
    element.addEventListener("wheel", (event) => {
      event.preventDefault();
      setValue(clamp(getValue() + (event.deltaY < 0 ? 0.025 : -0.025), 0, 1), { record: true });
    }, { passive: false });
    element.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown" && event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const direction = event.key === "ArrowUp" || event.key === "ArrowRight" ? 1 : -1;
      setValue(clamp(getValue() + direction * 0.02, 0, 1), { record: true });
    });
  }

  function initControls() {
    const nodes = $("#stringNodes");
    for (let index = 0; index < 17; index += 1) nodes.appendChild(document.createElement("i"));

    $("#pitchSlider").addEventListener("input", (event) => setPitch(Number(event.target.value)));
    $("#pitchSlider").addEventListener("change", () => recordParam("midi", state.midi));
    $("#octaveDown").addEventListener("click", () => setPitch(state.midi - 12, { record: true }));
    $("#octaveUp").addEventListener("click", () => setPitch(state.midi + 12, { record: true }));

    $("#pluckSlider").addEventListener("input", (event) => setPluckPosition(Number(event.target.value)));
    $("#pluckSlider").addEventListener("change", () => recordParam("position", state.position));

    setupKnob(
      $("#dampingKnob"),
      () => state.damping,
      (value, options) => setDamping(value, options)
    );
    setupKnob(
      $("#excitationKnob"),
      () => state.hardness,
      (value, options) => setHardness(value, options)
    );

    $("#notchASlider").addEventListener("input", (event) => {
      state.notchA = clamp(Number(event.target.value), 0.05, 0.48);
      $("#notchAValue").textContent = "β " + state.notchA.toFixed(2);
      queueNotchAnalysis();
    });
    $("#notchBSlider").addEventListener("input", (event) => {
      state.notchB = clamp(Number(event.target.value), 0.05, 0.48);
      $("#notchBValue").textContent = "β " + state.notchB.toFixed(2);
      queueNotchAnalysis();
    });
    $("#notchASlider").value = state.notchA.toFixed(2);
    $("#notchBSlider").value = state.notchB.toFixed(2);
    $("#notchAValue").textContent = "β " + state.notchA.toFixed(2);
    $("#notchBValue").textContent = "β " + state.notchB.toFixed(2);

    $("#muteButton").addEventListener("click", () => {
      state.muted = !state.muted;
      $("#muteButton").textContent = state.muted ? "Unmute" : "Mute";
      if (state.masterGain) {
        state.masterGain.gain.setTargetAtTime(
          state.muted ? 0 : 0.78,
          state.audioContext.currentTime,
          0.015
        );
      }
    });

    $("#pluckButton").addEventListener("click", () => triggerPluck());
    $("#auditButton").addEventListener("click", runPitchAudit);
    $("#replayButton").addEventListener("click", toggleReplay);
    $("#clearEventsButton").addEventListener("click", clearEvents);
    $("#loadDemoButton").addEventListener("click", loadDemo);
    $("#exportEventsButton").addEventListener("click", exportEvents);
    $("#exportWavButton").addEventListener("click", exportCurrentWav);
    $("#exportStripWavButton").addEventListener("click", exportTimelineWav);

    document.addEventListener("keydown", (event) => {
      if (event.code !== "Space" || event.repeat) return;
      const tag = event.target.tagName;
      if (tag === "INPUT" || tag === "BUTTON" || tag === "TEXTAREA") return;
      event.preventDefault();
      triggerPluck();
    });

    $$(".mode-link").forEach((button) => {
      button.addEventListener("click", () => {
        const section = document.getElementById(button.dataset.section);
        if (section) section.scrollIntoView({ behavior: "smooth", block: "start" });
        $$(".mode-link").forEach((item) => item.classList.toggle("is-active", item === button));
      });
    });

    setPitch(state.midi);
    setDamping(state.damping);
    setHardness(state.hardness);
    setPluckPosition(state.position);
    loadDemo(false);
  }

  function triggerPluck(options) {
    const settings = options || {};
    const manual = settings.record !== false;
    if (manual && state.replaying) stopReplay(true);
    if (manual && state.isDemo) beginUserRecording();
    const eventTime = manual ? eventTimeNow() : (settings.time || 0);
    const seed = settings.seed == null ? makeSeed() : settings.seed;
    const render = Core.renderNote({
      sampleRate: SAMPLE_RATE,
      frequency: state.frequency,
      position: state.position,
      damping: state.damping,
      hardness: state.hardness,
      velocity: state.velocity,
      seed
    });

    state.currentRender = render;
    state.currentRenderStartedAt = performance.now();
    $("#peakMetric").textContent = signed(20 * Math.log10(Math.max(1e-6, render.stats.peak)), 1) + " dB";
    $("#tailMetric").textContent = render.stats.t60.toFixed(1);
    $("#decayNote").textContent = "T60 " + render.stats.t60.toFixed(2) + " s · damping " + Math.round(state.damping * 100) + "%";
    $("#decayEmpty").classList.add("is-hidden");
    drawDecayPlot(render);
    playRenderedNote(render);

    const button = $("#pluckButton");
    button.classList.remove("is-struck");
    void button.offsetWidth;
    button.classList.add("is-struck");

    if (manual) {
      addEvent({
        id: newId("pluck"),
        type: "pluck",
        time: eventTime,
        midi: state.midi,
        frequency: state.frequency,
        position: state.position,
        damping: state.damping,
        hardness: state.hardness,
        velocity: state.velocity,
        seed
      });
    }
  }

  function fitCanvas(canvas) {
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(1, rect.width);
    const height = Math.max(1, rect.height);
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }
    const context = canvas.getContext("2d");
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, width, height);
    return { context, width, height };
  }

  function drawDecayPlot(render) {
    const canvas = $("#decayCanvas");
    const fitted = fitCanvas(canvas);
    const context = fitted.context;
    const width = fitted.width;
    const height = fitted.height;
    if (!render) return;

    const left = 34;
    const top = 12;
    const right = 9;
    const bottom = 18;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const displaySeconds = 4;
    const envelope = render.envelope.data;
    const peak = render.stats.peak || 1e-8;
    const hopSeconds = render.envelope.hopSize / render.envelope.sampleRate;

    context.save();
    context.beginPath();
    context.rect(left, top, plotWidth, plotHeight);
    context.clip();

    const points = [];
    for (let index = 0; index < envelope.length; index += 1) {
      const time = index * hopSeconds;
      if (time > displaySeconds) break;
      const level = 20 * Math.log10(Math.max(1e-9, envelope[index] / peak));
      const x = left + (time / displaySeconds) * plotWidth;
      const y = top + (clamp(-level, 0, 72) / 72) * plotHeight;
      points.push([x, y]);
    }

    if (points.length) {
      const gradient = context.createLinearGradient(0, top, 0, top + plotHeight);
      gradient.addColorStop(0, "rgba(241, 170, 97, 0.20)");
      gradient.addColorStop(0.55, "rgba(241, 170, 97, 0.05)");
      gradient.addColorStop(1, "rgba(241, 170, 97, 0)");
      context.beginPath();
      context.moveTo(points[0][0], top + plotHeight);
      for (const point of points) context.lineTo(point[0], point[1]);
      context.lineTo(points[points.length - 1][0], top + plotHeight);
      context.closePath();
      context.fillStyle = gradient;
      context.fill();

      context.beginPath();
      points.forEach((point, index) => {
        if (index === 0) context.moveTo(point[0], point[1]);
        else context.lineTo(point[0], point[1]);
      });
      context.strokeStyle = "#f1aa61";
      context.lineWidth = 1.5;
      context.stroke();
    }

    const t60X = left + clamp(render.stats.t60 / displaySeconds, 0, 1) * plotWidth;
    context.setLineDash([3, 4]);
    context.strokeStyle = "rgba(139, 216, 192, 0.48)";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(t60X, top);
    context.lineTo(t60X, top + plotHeight);
    context.stroke();
    context.setLineDash([]);

    const floorY = top + plotHeight * (60 / 72);
    context.strokeStyle = "rgba(223, 228, 204, 0.13)";
    context.setLineDash([2, 4]);
    context.beginPath();
    context.moveTo(left, floorY);
    context.lineTo(left + plotWidth, floorY);
    context.stroke();
    context.setLineDash([]);
    context.restore();

    if (t60X < width - 38) {
      context.fillStyle = "rgba(139, 216, 192, 0.75)";
      context.font = "7px SFMono-Regular, monospace";
      context.fillText("T60 " + render.stats.t60.toFixed(2) + "s", t60X + 4, top + 10);
    }
  }

  function drawLoopDiagram(now) {
    const canvas = $("#delayCanvas");
    const fitted = fitCanvas(canvas);
    const context = fitted.context;
    const width = fitted.width;
    const height = fitted.height;
    const active = state.activeSources.size > 0 || state.replaying;
    const phase = active ? now * 0.006 : 0;
    const left = 14;
    const right = width - 14;
    const top = 27;
    const bottom = 82;

    context.save();
    context.strokeStyle = "rgba(223, 228, 204, 0.18)";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(left + 6, top);
    context.lineTo(right - 6, top);
    context.quadraticCurveTo(right, top, right, top + 6);
    context.lineTo(right, bottom - 6);
    context.quadraticCurveTo(right, bottom, right - 6, bottom);
    context.lineTo(left + 6, bottom);
    context.quadraticCurveTo(left, bottom, left, bottom - 6);
    context.lineTo(left, top + 6);
    context.quadraticCurveTo(left, top, left + 6, top);
    context.stroke();

    const waveLength = Math.max(30, width * 0.2);
    const amplitude = active ? 6 : 1.3;
    const drawWave = (start, end, baseline, direction, alpha, dashPattern) => {
      context.beginPath();
      for (let x = start; x <= end; x += 1) {
        const position = direction > 0 ? x - start : end - x;
        const envelope = Math.sin(Math.PI * (x - start) / (end - start));
        const y = baseline + Math.sin((position / waveLength) * Math.PI * 2 - phase * direction)
          * amplitude * envelope;
        if (x === start) context.moveTo(x, y);
        else context.lineTo(x, y);
      }
      context.strokeStyle = "rgba(241, 170, 97, " + alpha + ")";
      context.lineWidth = 1.2;
      context.setLineDash(dashPattern || []);
      context.stroke();
      context.setLineDash([]);
    };

    drawWave(left + 25, right - 25, top, 1, active ? 0.9 : 0.36);
    drawWave(left + 25, right - 25, bottom, -1, active ? 0.42 : 0.18, [3, 4]);

    const components = [
      { x: width * 0.24, label: "delay · " + $("#delaySamples").textContent },
      { x: width * 0.51, label: "φ" },
      { x: width * 0.75, label: "H(z)" }
    ];
    for (const component of components) {
      context.fillStyle = "rgba(14, 16, 13, 0.96)";
      context.fillRect(component.x - 16, top - 8, 32, 16);
      context.strokeStyle = "rgba(223, 228, 204, 0.2)";
      context.strokeRect(component.x - 16, top - 8, 32, 16);
      context.fillStyle = "rgba(194, 196, 182, 0.8)";
      context.font = "6px SFMono-Regular, monospace";
      context.textAlign = "center";
      context.fillText(component.label, component.x, top + 2);
    }
    context.textAlign = "left";

    if (active) {
      const pulse = (now * 0.00028) % 1;
      const x = left + 25 + pulse * (right - left - 50);
      context.beginPath();
      context.arc(x, top, 2.2, 0, Math.PI * 2);
      context.fillStyle = "#ffc078";
      context.shadowBlur = 10;
      context.shadowColor = "#f1aa61";
      context.fill();
    }
    context.restore();
  }

  function queueNotchAnalysis() {
    window.clearTimeout(state.notchTimer);
    state.notchTimer = window.setTimeout(runNotchAnalysis, 90);
  }

  function runNotchAnalysis() {
    const result = Core.comparePluckPoints({
      sampleRate: SAMPLE_RATE,
      frequency: state.frequency,
      damping: state.damping,
      hardness: state.hardness,
      positionA: state.notchA,
      positionB: state.notchB,
      harmonicCount: 12
    });
    state.notchResult = result;
    drawNotchChart(result);
  }

  function drawNotchChart(result) {
    const canvas = $("#notchCanvas");
    const fitted = fitCanvas(canvas);
    const context = fitted.context;
    const width = fitted.width;
    const height = fitted.height;
    const left = 5;
    const right = 5;
    const top = 7;
    const bottom = 16;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const harmonics = result.harmonics;
    const groupWidth = plotWidth / harmonics.length;
    const barWidth = Math.min(6, Math.max(2.5, groupWidth * 0.17));
    const floorDb = -65;

    context.fillStyle = "rgba(139, 216, 192, 0.05)";
    context.fillRect(
      left + (result.deepest.harmonic - 1) * groupWidth,
      top,
      groupWidth,
      plotHeight
    );

    for (const harmonic of harmonics) {
      const center = left + (harmonic.harmonic - 0.5) * groupWidth;
      const heightA = clamp(-harmonic.dbA / -floorDb, 0.015, 1) * plotHeight;
      const heightB = clamp(-harmonic.dbB / -floorDb, 0.015, 1) * plotHeight;
      context.fillStyle = "rgba(241, 170, 97, 0.72)";
      context.fillRect(center - barWidth - 1, top + plotHeight - heightA, barWidth, heightA);
      context.fillStyle = "rgba(139, 216, 192, 0.78)";
      context.fillRect(center + 1, top + plotHeight - heightB, barWidth, heightB);
      context.fillStyle = harmonic.harmonic === result.deepest.harmonic
        ? "rgba(241, 170, 97, 0.95)"
        : "rgba(137, 141, 125, 0.75)";
      context.font = "6px SFMono-Regular, monospace";
      context.textAlign = "center";
      context.fillText(String(harmonic.harmonic), center, height - 3);
    }
    context.textAlign = "left";

    const readout = $("#notchReadout");
    const notchA = result.deepest;
    readout.innerHTML = "<span><i class=\"dot dot-b\"></i> H" + notchA.harmonic
      + " <b>" + notchA.dbB.toFixed(1) + " dB</b> at B</span><span id=\"notchSummary\">"
      + Math.abs(notchA.difference).toFixed(1) + " dB notch depth</span>";
    $("#notchEmpty").classList.add("is-hidden");
  }

  function queuePitchAudit() {
    window.clearTimeout(state.auditTimer);
    state.auditTimer = window.setTimeout(runPitchAudit, 700);
  }

  async function runPitchAudit() {
    if (state.auditRunning) return;
    state.auditRunning = true;
    const button = $("#auditButton");
    button.disabled = true;
    button.innerHTML = "<span class=\"audit-icon\">◌</span> Tracking";
    $("#auditPass").textContent = "RUNNING";
    $("#auditPass").className = "audit-pass";

    let worst = 0;
    for (const midi of AUDIT_TARGETS) {
      const row = $(".audit-row[data-midi=\"" + midi + "\"]");
      row.className = "audit-row is-running";
      row.querySelector(".audit-error").textContent = "…";
      row.querySelector(".audit-state").textContent = "scan";
      await new Promise((resolve) => window.setTimeout(resolve, 24));

      const targetFrequency = frequencyForMidi(midi);
      const render = Core.renderNote({
        sampleRate: SAMPLE_RATE,
        frequency: targetFrequency,
        position: 0.2,
        damping: state.damping,
        hardness: state.hardness,
        velocity: 0.78,
        seed: 0x4a10 + midi,
        duration: 1.5
      });
      const tracked = Core.spectralPitchTrack(render.samples, SAMPLE_RATE, targetFrequency);
      const cents = tracked.cents;
      worst = Math.max(worst, Math.abs(cents));
      const pass = Math.abs(cents) <= 3;
      row.className = "audit-row " + (pass ? "is-pass" : "is-fail");
      row.querySelector(".audit-error").textContent = signed(cents, 2) + "¢";
      row.querySelector(".audit-state").textContent = pass ? "pass" : "check";
      const bar = row.querySelector(".audit-bar i");
      const magnitude = clamp(Math.abs(cents) / 3, 0, 1);
      const barWidth = Math.max(2, magnitude * 46);
      bar.style.width = barWidth.toFixed(1) + "%";
      bar.style.left = cents < 0 ? (50 - barWidth).toFixed(1) + "%" : "50%";
      bar.style.background = pass ? "#8bd8c0" : "#e27564";
      row.dataset.measured = String(tracked.measuredFrequency);
      row.dataset.cents = String(cents);
      await new Promise((resolve) => window.requestAnimationFrame(resolve));
    }

    $("#worstError").textContent = worst.toFixed(2) + "¢";
    const pass = worst <= 3;
    $("#auditPass").textContent = pass ? "PASS ±3¢" : "REVIEW";
    $("#auditPass").className = "audit-pass " + (pass ? "is-pass" : "is-fail");
    button.disabled = false;
    button.innerHTML = "<span class=\"audit-icon\">↻</span> Run sweep";
    state.auditRunning = false;
  }

  function renderTimeline() {
    const events = state.events;
    const plucks = events.filter((event) => event.type === "pluck");
    const parameterEvents = events.filter((event) => event.type === "param");
    const lastTime = events.length ? Math.max(...events.map((event) => event.time)) : 0;
    const maxTime = Math.max(4, lastTime + 0.8);
    state.timelineMax = maxTime;
    const container = $("#timelineEvents");
    container.innerHTML = "";

    const fragment = document.createDocumentFragment();
    for (const event of events) {
      const item = document.createElement("div");
      const isParam = event.type === "param";
      item.className = "timeline-event" + (isParam ? " is-param" : "");
      item.style.left = clamp((event.time / maxTime) * 100, 0, 99.8).toFixed(3) + "%";
      if (isParam) {
        item.title = event.name + " = " + event.value;
      } else {
        const width = Math.max(1.5, (0.24 / maxTime) * 100);
        item.style.width = width.toFixed(2) + "%";
        item.textContent = noteName(event.midi || Core.frequencyToMidi(event.frequency));
        item.title = eventTimeTitle(event);
      }
      fragment.appendChild(item);
    }
    container.appendChild(fragment);
    $("#timelineEmpty").classList.toggle("is-hidden", events.length > 0);
    $("#timeline").classList.toggle("is-empty", events.length === 0);
    $("#eventCount").textContent = (state.isDemo ? "DEMO · " : "")
      + plucks.length + " pluck" + (plucks.length === 1 ? "" : "s")
      + (parameterEvents.length ? " · " + parameterEvents.length + " moves" : "")
      + " · " + lastTime.toFixed(2) + " s";
    if (!state.replaying) $("#eventTime").textContent = formatTime(lastTime);
    updateTimelineProgress(0);
  }

  function eventTimeTitle(event) {
    return noteName(event.midi) + " · " + formatFrequency(event.frequency) + " Hz · β "
      + Number(event.position).toFixed(2) + " · damping " + Math.round(event.damping * 100) + "%";
  }

  function updateTimelineProgress(seconds, visible) {
    const maximum = state.timelineMax || 4;
    const fraction = clamp(seconds / maximum, 0, 1);
    $("#timelineProgress").style.width = (fraction * 100).toFixed(3) + "%";
    const playhead = $("#timelinePlayhead");
    playhead.style.left = (fraction * 100).toFixed(3) + "%";
    playhead.classList.toggle("is-visible", Boolean(visible));
  }

  function clearEvents() {
    stopReplay(false);
    state.events = [];
    state.isDemo = false;
    state.recordingActive = false;
    renderTimeline();
    showToast("Event timeline cleared.");
  }

  function loadDemo(notify) {
    stopReplay(false);
    state.events = createDemoEvents();
    state.isDemo = true;
    state.recordingActive = false;
    const first = state.events[0];
    setPitch(first.midi);
    setPluckPosition(first.position);
    setDamping(first.damping);
    setHardness(first.hardness);
    state.velocity = first.velocity;
    renderTimeline();
    if (notify !== false) showToast("Demo event set loaded. Press play to replay.");
  }

  function applyReplayEvent(event) {
    if (event.type === "param") {
      if (event.name === "midi") setPitch(event.value);
      if (event.name === "position") setPluckPosition(event.value);
      if (event.name === "damping") setDamping(event.value);
      if (event.name === "hardness") setHardness(event.value);
      return;
    }
    if (event.midi != null) setPitch(event.midi);
    else if (event.frequency != null) {
      state.frequency = event.frequency;
      state.midi = Math.round(Core.frequencyToMidi(event.frequency));
    }
    if (event.position != null) setPluckPosition(event.position);
    if (event.damping != null) setDamping(event.damping);
    if (event.hardness != null) setHardness(event.hardness);
    if (event.velocity != null) state.velocity = event.velocity;
    triggerPluck({ record: false, time: event.time, seed: event.seed });
  }

  function startReplay() {
    if (!state.events.length) {
      showToast("No events to replay. Pluck the string or load the demo.");
      return;
    }
    if (state.replaying) return;
    ensureAudio();
    state.replaying = true;
    state.replayStartedAt = performance.now();
    $("#replayButton").classList.add("is-playing");
    $("#replayButton").setAttribute("aria-label", "Stop replay");
    state.replayTimers.forEach((timer) => window.clearTimeout(timer));
    state.replayTimers = [];

    for (const event of state.events) {
      const timer = window.setTimeout(() => {
        if (!state.replaying) return;
        applyReplayEvent(event);
      }, event.time * 1000);
      state.replayTimers.push(timer);
    }

    const tick = () => {
      if (!state.replaying) return;
      const elapsed = (performance.now() - state.replayStartedAt) / 1000;
      const maximum = state.timelineMax || 4;
      $("#eventTime").textContent = formatTime(Math.min(elapsed, maximum));
      updateTimelineProgress(elapsed, true);
      if (elapsed >= maximum + 0.15) {
        stopReplay(false);
        return;
      }
      state.replayAnimation = window.requestAnimationFrame(tick);
    };
    state.replayAnimation = window.requestAnimationFrame(tick);
  }

  function stopReplay(stopAudio) {
    state.replaying = false;
    window.cancelAnimationFrame(state.replayAnimation);
    state.replayTimers.forEach((timer) => window.clearTimeout(timer));
    state.replayTimers = [];
    $("#replayButton").classList.remove("is-playing");
    $("#replayButton").setAttribute("aria-label", "Replay events");
    updateTimelineProgress(0, false);
    if (stopAudio !== false) stopAllAudio();
  }

  function toggleReplay() {
    if (state.replaying) stopReplay(true);
    else startReplay();
  }

  function exportEvents() {
    if (!state.events.length) {
      showToast("No parameter events to export.");
      return;
    }
    const payload = {
      format: "waveguide-string-events",
      version: 1,
      engine: "WG-04 digital waveguide",
      createdAt: new Date().toISOString(),
      sampleRate: SAMPLE_RATE,
      a4: 440,
      synthesis: {
        delayLine: true,
        fractionalAllpass: true,
        frequencyDependentLoss: true,
        pluckPosition: true,
        proceduralExcitation: true
      },
      events: state.events
    };
    downloadBlob(
      new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }),
      "waveguide-events.json"
    );
    showToast("Exported " + state.events.length + " replayable parameter events.");
  }

  function exportCurrentWav() {
    const render = Core.renderNote({
      sampleRate: SAMPLE_RATE,
      frequency: state.frequency,
      position: state.position,
      damping: state.damping,
      hardness: state.hardness,
      velocity: state.velocity,
      seed: 0x4440
    });
    const filename = "waveguide-" + noteName(state.midi) + "-" + Math.round(state.frequency) + "hz.wav";
    downloadWav(render.samples, filename);
    showToast("Rendered " + filename + " · " + render.duration.toFixed(2) + " s · 16-bit PCM.");
  }

  function exportTimelineWav() {
    if (!state.events.length) {
      showToast("The timeline is empty.");
      return;
    }
    const timeline = Core.renderTimeline(state.events, { sampleRate: SAMPLE_RATE });
    if (!timeline.samples.length) {
      showToast("No pluck events in the timeline.");
      return;
    }
    downloadWav(timeline.samples, "waveguide-timeline.wav");
    showToast("Rendered timeline WAV · " + timeline.duration.toFixed(2) + " s · " + timeline.notes.length + " procedural plucks.");
  }

  function animateVisuals() {
    const now = performance.now();
    drawLoopDiagram(now);

    const cursor = $("#decayCursor");
    if (state.currentRender) {
      const elapsed = (now - state.currentPlayStart) / 1000;
      if (elapsed >= 0 && elapsed < 4) {
        cursor.classList.add("is-visible");
        cursor.style.left = ((elapsed / 4) * 100).toFixed(3) + "%";
      } else {
        cursor.classList.remove("is-visible");
      }
    } else {
      cursor.classList.remove("is-visible");
    }
    window.requestAnimationFrame(animateVisuals);
  }

  function redrawAnalyses() {
    if (state.currentRender) drawDecayPlot(state.currentRender);
    if (state.notchResult) drawNotchChart(state.notchResult);
  }

  function init() {
    initControls();
    runNotchAnalysis();
    window.setTimeout(runPitchAudit, 420);
    window.addEventListener("resize", redrawAnalyses);
    if (window.ResizeObserver) {
      const observer = new ResizeObserver(redrawAnalyses);
      observer.observe($("#decayCanvas"));
      observer.observe($("#notchCanvas"));
    }
    window.requestAnimationFrame(animateVisuals);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
