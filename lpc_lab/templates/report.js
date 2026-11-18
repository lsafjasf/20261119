"use strict";

const DATA = JSON.parse(document.getElementById("lab-data").textContent);
const state = { signal: "vowel", window: 320, order: 12 };
const COLORS = {
  ink: "#e8f2f5", muted: "#8ba3b2", line: "#254052",
  cyan: "#5de4d0", lime: "#b8ef5a", orange: "#ffb15c",
  red: "#ff7185", blue: "#6db5ff", grid: "rgba(139,163,178,.18)"
};

const $ = (id) => document.getElementById(id);
const fmt = (value, digits = 2) =>
  Number.isFinite(value) ? Number(value).toFixed(digits) : "—";
const sci = (value) =>
  Number.isFinite(value) ? Number(value).toExponential(2) : "—";

function optionsFor(key) {
  return [...new Set(DATA.results.map((item) => item[key]))].sort((a, b) => a - b);
}

function resultsFor(signal, windowLength) {
  return DATA.results
    .filter((item) => item.signal === signal && item.window_length === windowLength)
    .sort((a, b) => a.order - b.order);
}

function currentResult() {
  return DATA.results.find(
    (item) =>
      item.signal === state.signal &&
      item.window_length === state.window &&
      item.order === state.order
  );
}

function fillSelect(select, values, selected) {
  select.innerHTML = "";
  for (const value of values) {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = String(value);
    if (value === selected) option.selected = true;
    select.appendChild(option);
  }
}

function rebuildControls() {
  const signals = [...new Set(DATA.results.map((item) => item.signal))];
  const labels = Object.fromEntries(DATA.results.map((item) => [item.signal, item.label]));
  const signalSelect = $("signal-select");
  signalSelect.innerHTML = "";
  for (const signal of signals) {
    const option = document.createElement("option");
    option.value = signal;
    option.textContent = labels[signal] || signal;
    option.selected = signal === state.signal;
    signalSelect.appendChild(option);
  }
  fillSelect($("window-select"), optionsFor("window_length"), state.window);
  const orders = resultsFor(state.signal, state.window).map((item) => item.order);
  fillSelect($("order-select"), orders, state.order);
}

function setupCanvas(canvas) {
  const width = Math.max(240, canvas.clientWidth);
  const height = Number(canvas.dataset.height || canvas.getAttribute("height") || 300);
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  canvas.style.height = `${height}px`;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return { ctx, width, height };
}

function drawGrid(ctx, x, y, width, height, rows = 4, columns = 6) {
  ctx.save();
  ctx.strokeStyle = COLORS.grid;
  ctx.lineWidth = 1;
  for (let row = 0; row <= rows; row += 1) {
    const py = y + (height * row) / rows;
    ctx.beginPath();
    ctx.moveTo(x, py);
    ctx.lineTo(x + width, py);
    ctx.stroke();
  }
  for (let column = 0; column <= columns; column += 1) {
    const px = x + (width * column) / columns;
    ctx.beginPath();
    ctx.moveTo(px, y);
    ctx.lineTo(px, y + height);
    ctx.stroke();
  }
  ctx.restore();
}

function drawPath(ctx, values, x, y, width, height, color, lineWidth = 1.6) {
  if (!values || !values.length) return;
  let minimum = Math.min(...values);
  let maximum = Math.max(...values);
  if (maximum - minimum < 1e-12) {
    maximum = minimum + 1e-9;
  }
  const padding = (maximum - minimum) * 0.08;
  minimum -= padding;
  maximum += padding;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.lineJoin = "round";
  ctx.beginPath();
  values.forEach((value, index) => {
    const px = x + (width * index) / Math.max(1, values.length - 1);
    const py = y + height * (1 - (value - minimum) / (maximum - minimum));
    if (index === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  ctx.stroke();
  ctx.restore();
}

function updateStats(result) {
  $("stat-snr").textContent = fmt(result.snr_db, 2);
  $("stat-mse").textContent = sci(result.mse);
  $("stat-rate").textContent = fmt(result.total_kilobits_per_second, 1);
  $("stat-ratio").textContent = `${fmt(result.compression_ratio, 2)}×`;
  $("stat-pole").textContent = fmt(result.stability.max_quantized_pole_radius, 4);
  const total = result.stability.frames;
  const unstable = result.stability.max_quantized_pole_radius >= 1;
  $("stat-stable").textContent = unstable ? "失稳" : `${total} / ${total}`;
  $("stat-stable").style.color = unstable ? COLORS.red : COLORS.cyan;
}

function updateSummary(result) {
  const detail = result.detail;
  const stable = detail.stable && result.stability.max_quantized_pole_radius < 1;
  const badge = $("stability-badge");
  badge.textContent = stable ? "单位圆内 · 稳定" : "需要稳定化";
  badge.className = `badge ${stable ? "stable" : "unstable"}`;
  $("frame-label").textContent =
    `frame ${detail.frame_index} · sample ${detail.frame_start} · 增益 ${fmt(detail.prediction_gain_db, 2)} dB`;
  $("current-combo").textContent =
    `${result.label} · ${result.window_length} 窗 · ${result.order} 阶`;
  const methods = Object.entries(result.stability.methods)
    .map(([name, count]) => `${name}: ${count}`)
    .join("，");
  $("summary").innerHTML = `
    <div><strong>分析配置</strong>：帧长 ${result.frame_length}，窗长 ${result.window_length}，汉宁窗，${result.order} 阶，预加重 0。</div>
    <div><strong>系数码流</strong>：反射系数，${result.coefficient_bits} bit/系数；${result.coefficient_file_bytes} B。</div>
    <div><strong>残差码流</strong>：逐帧块浮点，${result.residual_bits} bit/样本；${result.residual_file_bytes} B。</div>
    <div><strong>重建</strong>：SNR ${fmt(result.snr_db, 2)} dB，MSE ${sci(result.mse)}，最大误差 ${sci(result.max_abs_error)}。</div>
    <div><strong>稳定性</strong>：最大量化极点半径 ${fmt(result.stability.max_quantized_pole_radius, 6)}；方法 ${methods || "—"}。</div>`;
  const links = $("stream-links");
  links.innerHTML = "";
  if (result.stream_prefix) {
    for (const [suffix, text] of [
      [".coef", "系数流 .coef"],
      [".res", "残差文件 .res"],
      [".recon.wav", "重建 WAV"]
    ]) {
      const anchor = document.createElement("a");
      anchor.href = `${result.stream_prefix}${suffix}`;
      anchor.textContent = text;
      links.appendChild(anchor);
    }
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "none";
    audio.src = `${result.stream_prefix}.recon.wav`;
    links.appendChild(audio);
  } else {
    links.textContent = "该窗长组合用于统计；默认 320 样本窗长保存了独立码流文件。";
  }
}

function update() {
  const result = currentResult();
  if (!result) return;
  $("config-note").textContent =
    `帧长 ${result.frame_length} 样本 / ${fmt(result.frame_length / result.scale_samples_per_second * 1000, 1)} ms · ${fmt(result.scale_samples_per_second / 1000, 1)} kHz · 汉宁窗`;
  $("coef-bits-note").textContent = String(result.coefficient_bits);
  $("residual-bits-note").textContent = String(result.residual_bits);
  updateStats(result);
  updateSummary(result);
  drawWave(result.detail, result.scale_samples_per_second);
  drawPoles(result.detail);
  drawMatrix(result.detail.autocorrelation_matrix, result.order);
  drawComparison();
  updateResultsTable();
  updateAuditTable();
}

function bindControls() {
  $("signal-select").addEventListener("change", (event) => {
    state.signal = event.target.value;
    rebuildControls();
    update();
  });
  $("window-select").addEventListener("change", (event) => {
    state.window = Number(event.target.value);
    const orders = resultsFor(state.signal, state.window).map((item) => item.order);
    if (!orders.includes(state.order)) state.order = orders[Math.floor(orders.length / 2)];
    rebuildControls();
    update();
  });
  $("order-select").addEventListener("change", (event) => {
    state.order = Number(event.target.value);
    update();
  });
  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(update, 120);
  });
}

rebuildControls();
bindControls();
update();

function drawWave(detail, sampleRate) {
  const canvas = $("wave-canvas");
  canvas.dataset.height = "320";
  const { ctx, width, height } = setupCanvas(canvas);
  const left = 42;
  const right = 14;
  const top = 18;
  const gap = 24;
  const bottom = 28;
  const usableWidth = width - left - right;
  const usableHeight = height - top - bottom - gap;
  const topHeight = usableHeight * 0.66;
  const bottomHeight = usableHeight * 0.34;
  const bottomTop = top + topHeight + gap;

  drawGrid(ctx, left, top, usableWidth, topHeight, 3, 6);
  drawGrid(ctx, left, bottomTop, usableWidth, bottomHeight, 2, 6);
  drawPath(
    ctx, detail.frame_samples, left, top, usableWidth, topHeight,
    COLORS.blue, 1.4
  );
  drawPath(
    ctx, detail.prediction_error, left, bottomTop, usableWidth, bottomHeight,
    COLORS.orange, 1.25
  );

  ctx.save();
  ctx.fillStyle = COLORS.muted;
  ctx.font = "10px ui-sans-serif, system-ui";
  ctx.textAlign = "left";
  ctx.fillText("PCM", left + 6, top + 13);
  ctx.fillText("误差", left + 6, bottomTop + 13);
  ctx.textAlign = "right";
  ctx.fillText("0", left - 6, top + topHeight / 2 + 3);
  ctx.fillText("0", left - 6, bottomTop + bottomHeight / 2 + 3);
  const durationMs = detail.frame_samples.length / sampleRate * 1000;
  ctx.textAlign = "center";
  for (let tick = 0; tick <= 5; tick += 1) {
    const x = left + usableWidth * tick / 5;
    ctx.fillText(`${fmt(durationMs * tick / 5, 1)} ms`, x, height - 8);
  }
  ctx.strokeStyle = COLORS.line;
  ctx.beginPath();
  ctx.moveTo(left, top + topHeight / 2);
  ctx.lineTo(left + usableWidth, top + topHeight / 2);
  ctx.moveTo(left, bottomTop + bottomHeight / 2);
  ctx.lineTo(left + usableWidth, bottomTop + bottomHeight / 2);
  ctx.stroke();
  ctx.restore();
}

function drawPoles(detail) {
  const canvas = $("pole-canvas");
  canvas.dataset.height = "320";
  const { ctx, width, height } = setupCanvas(canvas);
  const size = Math.min(width - 80, height - 65);
  const radius = size / 2;
  const centerX = width / 2;
  const centerY = 28 + radius;

  ctx.save();
  ctx.translate(centerX, centerY);
  ctx.strokeStyle = COLORS.grid;
  ctx.lineWidth = 1;
  for (const fraction of [0.25, 0.5, 0.75, 1]) {
    ctx.beginPath();
    ctx.arc(0, 0, radius * fraction, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.strokeStyle = COLORS.line;
  ctx.beginPath();
  ctx.moveTo(-radius - 12, 0);
  ctx.lineTo(radius + 12, 0);
  ctx.moveTo(0, -radius - 12);
  ctx.lineTo(0, radius + 12);
  ctx.stroke();
  ctx.strokeStyle = COLORS.cyan;
  ctx.lineWidth = 1.8;
  ctx.beginPath();
  ctx.arc(0, 0, radius, 0, Math.PI * 2);
  ctx.stroke();

  for (const pole of detail.poles) {
    const x = pole.real * radius;
    const y = -pole.imag * radius;
    ctx.fillStyle = "rgba(255,177,92,.18)";
    ctx.strokeStyle = COLORS.orange;
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  ctx.fillStyle = COLORS.muted;
  ctx.font = "11px ui-sans-serif, system-ui";
  ctx.textAlign = "center";
  ctx.fillText("Re", radius - 5, 16);
  ctx.fillText("Im", 17, -radius + 5);
  ctx.fillStyle = COLORS.ink;
  ctx.textAlign = "left";
  ctx.fillText(`|p|max = ${fmt(detail.pole_radius, 5)}`, -radius, radius + 28);
  ctx.restore();
}

function drawMatrix(matrix, order) {
  const table = $("matrix-table");
  let maximum = 0;
  for (const row of matrix) {
    for (const value of row) maximum = Math.max(maximum, Math.abs(value));
  }
  const header = ["i\\j", ...Array.from({ length: order + 1 }, (_, index) => index)];
  const parts = [`<thead><tr>${header.map((label) => `<th>${label}</th>`).join("")}</tr></thead><tbody>`];
  matrix.forEach((row, rowIndex) => {
    const cells = row.map((value, columnIndex) => {
      const weight = maximum
        ? Math.log1p(Math.abs(value) / maximum * 99) / Math.log(100)
        : 0;
      const background = `rgba(93,228,208,${(0.025 + 0.48 * weight).toFixed(3)})`;
      const text = Math.abs(value) >= 1000 ? sci(value) : fmt(value, 3);
      const title = `R[${rowIndex}][${columnIndex}] = r[${Math.abs(rowIndex - columnIndex)}] = ${value}`;
      return `<td title="${title}" style="background:${background}">${text}</td>`;
    });
    parts.push(`<tr><th>${rowIndex}</th>${cells.join("")}</tr>`);
  });
  parts.push("</tbody>");
  table.innerHTML = parts.join("");
}

function drawComparison() {
  const canvas = $("compare-canvas");
  canvas.dataset.height = "280";
  const { ctx, width, height } = setupCanvas(canvas);
  const rows = resultsFor(state.signal, state.window);
  if (!rows.length) return;
  const left = 48;
  const right = 56;
  const top = 20;
  const bottom = 34;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const snrs = rows.map((row) => row.snr_db);
  const rates = rows.map((row) => row.total_kilobits_per_second);
  let snrMin = Math.min(...snrs);
  let snrMax = Math.max(...snrs);
  let rateMin = Math.min(...rates);
  let rateMax = Math.max(...rates);
  if (snrMax - snrMin < 1) { snrMin -= 0.5; snrMax += 0.5; }
  if (rateMax - rateMin < 1) { rateMin -= 0.5; rateMax += 0.5; }
  const snrPad = (snrMax - snrMin) * 0.12;
  const ratePad = (rateMax - rateMin) * 0.12;
  snrMin -= snrPad; snrMax += snrPad;
  rateMin -= ratePad; rateMax += ratePad;
  drawGrid(ctx, left, top, plotWidth, plotHeight, 4, Math.max(1, rows.length - 1));

  const xAt = (index) =>
    left + (rows.length === 1 ? plotWidth / 2 : plotWidth * index / (rows.length - 1));
  const ySnr = (value) => top + plotHeight * (1 - (value - snrMin) / (snrMax - snrMin));
  const yRate = (value) => top + plotHeight * (1 - (value - rateMin) / (rateMax - rateMin));

  const line = (values, yAt, color) => {
    ctx.save();
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    values.forEach((value, index) => {
      const x = xAt(index);
      const y = yAt(value);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    values.forEach((value, index) => {
      ctx.beginPath();
      ctx.arc(xAt(index), yAt(value), 4, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.restore();
  };
  line(snrs, ySnr, COLORS.lime);
  line(rates, yRate, COLORS.blue);

  ctx.save();
  ctx.fillStyle = COLORS.muted;
  ctx.font = "10px ui-sans-serif, system-ui";
  ctx.textAlign = "right";
  for (let tick = 0; tick <= 4; tick += 1) {
    const value = snrMax - (snrMax - snrMin) * tick / 4;
    ctx.fillText(fmt(value, 1), left - 7, top + plotHeight * tick / 4 + 3);
  }
  ctx.textAlign = "left";
  for (let tick = 0; tick <= 4; tick += 1) {
    const value = rateMax - (rateMax - rateMin) * tick / 4;
    ctx.fillText(fmt(value, 0), left + plotWidth + 7, top + plotHeight * tick / 4 + 3);
  }
  ctx.textAlign = "center";
  rows.forEach((row, index) => {
    ctx.fillText(`p=${row.order}`, xAt(index), height - 12);
  });
  ctx.textAlign = "left";
  ctx.fillStyle = COLORS.lime;
  ctx.fillText("SNR dB", 5, 12);
  ctx.textAlign = "right";
  ctx.fillStyle = COLORS.blue;
  ctx.fillText("kbit/s", width - 5, 12);
  ctx.restore();
}

function updateResultsTable() {
  const rows = DATA.results
    .filter((item) => item.signal === state.signal)
    .sort((a, b) => a.window_length - b.window_length || a.order - b.order);
  const body = $("results-table").querySelector("tbody");
  body.innerHTML = rows.map((row) => {
    const active =
      row.window_length === state.window && row.order === state.order ? "current" : "";
    const stable = row.stability.max_quantized_pole_radius < 1 ? "good" : "warn";
    return `<tr class="${active}">
      <td>${row.window_length}</td>
      <td>${row.order}</td>
      <td>${fmt(row.snr_db, 2)}</td>
      <td>${sci(row.mse)}</td>
      <td>${row.coefficient_file_bytes}</td>
      <td>${row.residual_file_bytes}</td>
      <td>${fmt(row.total_kilobits_per_second, 1)}</td>
      <td class="${stable}">${stable === "good" ? "✓" : "✗"}</td>
    </tr>`;
  }).join("");
  const label = rows.length ? rows[0].label : state.signal;
  $("table-caption").textContent = `${label} · 全部窗长 · 当前组合高亮`;
}

function methodName(name) {
  const names = {
    rc_inherently_stable: "反射系数量化，固有稳定",
    rc_shrink_numeric: "反射系数数值收缩",
    rc_rejected_to_zero: "反射系数拒绝并归零",
    direct_stable: "直接量化后稳定",
    coefficient_scaling: "系数逐次 ×0.95 收缩",
    rejected_to_zero: "无法稳定化，拒绝并归零"
  };
  return names[name] || name;
}

function updateAuditTable() {
  const audits = DATA.stability_audit ? DATA.stability_audit.audits : [];
  const body = $("audit-table").querySelector("tbody");
  body.innerHTML = audits.map((row) => {
    const method = Object.entries(row.methods)
      .map(([name, count]) => `${methodName(name)} ×${count}`)
      .join("；");
    const silencePeak = row.silence_output_peak === null
      ? "—"
      : fmt(row.silence_output_peak, 6);
    const radiusClass = row.max_quantized_pole_radius < 1 ? "good" : "warn";
    return `<tr>
      <td>${row.label}</td>
      <td>${row.mode === "rc" ? "反射" : "直接"} ${row.coefficient_bits}b</td>
      <td>${row.unstable_detected}</td>
      <td>${row.stabilized}</td>
      <td>${row.rejected}</td>
      <td class="${radiusClass}">${fmt(row.max_quantized_pole_radius, 6)}</td>
      <td>${method || "—"}</td>
      <td>${silencePeak}</td>
    </tr>`;
  }).join("");
}

function updateMethodNotes() {
  const policy = DATA.stability_audit ? DATA.stability_audit.policy : {};
  $("method-notes").innerHTML = `
    <div><strong>分析</strong>：PCM 按 400 样本分帧（25 ms），在帧内居中加入指定长度汉宁窗；计算 r[k]=Σx[n]x[n−k]，组成 Toeplitz 矩阵后由 Levinson-Durbin 求解预测器 a[1..p]。预测误差 e[n]=x[n]−Σa[i]x[n−i]。</div>
    <div><strong>稳定性</strong>：${policy.rc || "反射系数位于 (-1,1) 内，理论上稳定。"} 静音帧的 r[0] 小于门限时直接使用零预测器，避免 Levinson 递推除零。</div>
    <div><strong>直接系数压力路径</strong>：${policy.direct || "量化后检测极点，失稳时收缩或拒绝并记录。"} 每一步处置均写入帧 flags 与 <code>stability.methods</code>。</div>
    <div><strong>码流</strong>：<code>.coef</code> 含 JSON 头、逐帧 flags 与按位打包的量化系数；<code>.res</code> 含 JSON 头、逐帧 float32 块缩放与量化残差。编码端用同一个量化预测器和已重建历史计算残差（闭环），解码行为与重建误差一致。</div>
    <div><strong>独立解码</strong>：<code>python3 decode_stream.py input.coef input.res output.wav</code>。解码器是单文件、仅依赖 Python 标准库，不导入本项目。</div>`;
}

updateMethodNotes();
update();
