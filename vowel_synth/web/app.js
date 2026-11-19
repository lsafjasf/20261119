"use strict";

const SAMPLE_RATE = 22050;
const SLIDERS = [
  { key: "f0", label: "基频 F0", min: 60, max: 500, step: 1, unit: "Hz" },
  { key: "f1", label: "F1", min: 120, max: 1200, step: 1, unit: "Hz" },
  { key: "bw1", label: "F1 带宽", min: 30, max: 600, step: 1, unit: "Hz" },
  { key: "f2", label: "F2", min: 400, max: 3000, step: 1, unit: "Hz" },
  { key: "bw2", label: "F2 带宽", min: 30, max: 600, step: 1, unit: "Hz" },
  { key: "f3", label: "F3", min: 1200, max: 4500, step: 1, unit: "Hz" },
  { key: "bw3", label: "F3 带宽", min: 30, max: 800, step: 1, unit: "Hz" },
  { key: "breathiness", label: "气声比例", min: 0, max: 1, step: 0.01, unit: "" },
  { key: "smooth_tau", label: "平滑时间", min: 0.005, max: 0.5, step: 0.005, unit: "s" },
];

const sliderEls = {};
const slidersRoot = document.getElementById("sliders");
for (const spec of SLIDERS) {
  const row = document.createElement("div");
  row.className = "slider-row";
  row.innerHTML =
    `<label>${spec.label}</label>` +
    `<input type="range" min="${spec.min}" max="${spec.max}" step="${spec.step}">` +
    `<output></output>`;
  const input = row.querySelector("input");
  const out = row.querySelector("output");
  input.addEventListener("input", () => {
    out.textContent = `${Number(input.value).toFixed(spec.step < 0.1 ? 3 : 0)} ${spec.unit}`;
    pushParams({ [spec.key]: Number(input.value) });
  });
  sliderEls[spec.key] = { input, out, spec };
  slidersRoot.appendChild(row);
}

let pushTimer = null;
const pending = {};
function pushParams(changes) {
  Object.assign(pending, changes);
  clearTimeout(pushTimer);
  pushTimer = setTimeout(async () => {
    const body = { ...pending };
    for (const k of Object.keys(pending)) delete pending[k];
    await fetch("/api/params", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }, 40);
}

async function syncSliders() {
  const res = await fetch("/api/params");
  const params = await res.json();
  for (const spec of SLIDERS) {
    const { input, out } = sliderEls[spec.key];
    input.value = params[spec.key];
    out.textContent = `${Number(params[spec.key]).toFixed(spec.step < 0.1 ? 3 : 0)} ${spec.unit}`;
  }
}
syncSliders();

document.querySelectorAll("[data-preset]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const res = await fetch("/api/preset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: btn.dataset.preset }),
    });
    if (res.ok) syncSliders();
  });
});

// ---- Streaming playback: the server keeps one continuous synthesis state.
let audioCtx = null;
let playing = false;
let nextStartTime = 0;
let pumpTimer = null;
const playBtn = document.getElementById("playBtn");
const playState = document.getElementById("playState");

async function pump() {
  if (!playing) return;
  const res = await fetch("/api/stream");
  const buffer = await res.arrayBuffer();
  if (buffer.byteLength > 0) {
    const int16 = new Int16Array(buffer);
    const pcm = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) pcm[i] = int16[i] / 32768;
    const audioBuffer = audioCtx.createBuffer(1, pcm.length, SAMPLE_RATE);
    audioBuffer.copyToChannel(pcm, 0);
    const node = audioCtx.createBufferSource();
    node.buffer = audioBuffer;
    node.connect(audioCtx.destination);
    const now = audioCtx.currentTime;
    nextStartTime = Math.max(now + 0.02, nextStartTime);
    node.start(nextStartTime);
    nextStartTime += audioBuffer.duration;
  }
}

playBtn.addEventListener("click", async () => {
  if (!playing) {
    audioCtx = audioCtx || new AudioContext({ sampleRate: SAMPLE_RATE });
    await audioCtx.resume();
    playing = true;
    nextStartTime = 0;
    playBtn.textContent = "■ 停止发声";
    playState.textContent = "连续播放中（调参不重启）";
    pump();
    pumpTimer = setInterval(pump, 90);
  } else {
    playing = false;
    clearInterval(pumpTimer);
    playBtn.textContent = "▶ 开始发声";
    playState.textContent = "已停止";
  }
});

// ---- Spectrum + analysis display.
const canvas = document.getElementById("spectrum");
const ctx = canvas.getContext("2d");

function drawLine(freqs, db, color, maxFreq = 5000) {
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  const w = canvas.width, h = canvas.height;
  let started = false;
  for (let i = 0; i < freqs.length; i++) {
    if (freqs[i] > maxFreq) break;
    const x = (freqs[i] / maxFreq) * w;
    const y = h - ((db[i] + 100) / 100) * h;
    if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
  }
  ctx.stroke();
}

function drawGrid() {
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "#1b2733";
  ctx.fillStyle = "#6f8296";
  ctx.font = "11px monospace";
  for (const f of [0, 1000, 2000, 3000, 4000, 5000]) {
    const x = (f / 5000) * w;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    ctx.fillText(`${f}`, x + 3, h - 5);
  }
}

function renderTable(formants) {
  const tbody = document.querySelector("#formantTable tbody");
  tbody.innerHTML = "";
  formants.forEach((f, i) => {
    const tr = document.createElement("tr");
    const nan = (v) => (Number.isNaN(v) ? "–" : v.toFixed(1));
    tr.innerHTML =
      `<td>F${i + 1}</td><td>${f.declared_f.toFixed(0)}</td>` +
      `<td>${nan(f.detected_f)}</td><td>${nan(f.error_hz)}</td>` +
      `<td>${f.declared_bw.toFixed(0)}</td><td>${nan(f.detected_bw)}</td>`;
    tbody.appendChild(tr);
  });
}

async function pollState() {
  try {
    const res = await fetch("/api/state");
    const s = await res.json();
    if (!s.freqs) return;
    drawGrid();
    drawLine(s.freqs, s.source_db, "#ffb454");
    drawLine(s.freqs, s.output_db, "#4cc2ff");
    drawLine(s.env_freqs, s.env_db, "#ff6b81");
    renderTable(s.formants);
    const v = s.voicing;
    if (!Number.isNaN(v.voiced_share)) {
      document.getElementById("voicedBar").style.width = `${v.voiced_share * 100}%`;
      document.getElementById("breathyBar").style.width = `${v.breathy_share * 100}%`;
      document.getElementById("voicedVal").textContent = `${(v.voiced_share * 100).toFixed(1)}%`;
      document.getElementById("breathyVal").textContent = `${(v.breathy_share * 100).toFixed(1)}%`;
      document.getElementById("hnrVal").textContent = v.hnr_db.toFixed(1);
    }
  } catch (err) {
    /* transient polling errors are ignored */
  }
}
setInterval(pollState, 200);
pollState();

async function doExport(extra) {
  const duration = Number(document.getElementById("duration").value);
  const res = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ duration, ...extra }),
  });
  const info = await res.json();
  document.getElementById("exportResult").textContent =
    `${info.wav} · ${info.csv}`;
}
document.getElementById("exportCurrent").addEventListener("click", () => doExport({}));
document.getElementById("exportTransition").addEventListener("click", async () => {
  const res = await fetch("/api/params");
  const current = await res.json();
  const keyframes = [
    [0, { ...current, ...PRESET_OVERRIDES.a }],
    [1.5, { ...current, ...PRESET_OVERRIDES.i }],
  ];
  doExport({ keyframes });
});

const PRESET_OVERRIDES = {
  a: { f1: 730, bw1: 80, f2: 1090, bw2: 90, f3: 2440, bw3: 120, breathiness: 0.15 },
  i: { f1: 270, bw1: 70, f2: 2290, bw2: 90, f3: 3010, bw3: 140, breathiness: 0.1 },
};
