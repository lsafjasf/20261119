(function () {
  "use strict";

  const M = window.JpegMath;
  const P = window.JpegPatterns;
  const BLOCK = M.N;
  const WIDTH = P.SIZE;
  const HEIGHT = P.SIZE;
  const BLOCKS_X = WIDTH / BLOCK;
  const BLOCKS_Y = HEIGHT / BLOCK;
  const UNIT_TABLE = M.makeUnitTable();

  const state = {
    pattern: "flat",
    sample: P.createSample("flat"),
    plane: "Y",
    blockX: 8,
    blockY: 8,
    lumaQuality: 75,
    chromaQuality: 75,
    lumaTable: M.makeQuantTable("luma", 75),
    chromaTable: M.makeQuantTable("chroma", 75),
    customLuma: false,
    customChroma: false,
    tableTab: "luma",
    selectedBasis: { u: 0, v: 0 },
    overrides: new Map(),
    current: null,
    planeRecon: null,
    planeMetrics: null,
  };

  const dom = {
    patternButtons: document.getElementById("patternButtons"),
    toolbarPlane: document.getElementById("toolbarPlane"),
    planeTabs: document.querySelector(".plane-tabs"),
    sourceCanvas: document.getElementById("sourceCanvas"),
    sourceBlockLabel: document.getElementById("sourceBlockLabel"),
    blockNavLabel: document.getElementById("blockNavLabel"),
    blockNav: document.querySelector(".block-nav"),
    planeMean: document.getElementById("planeMean"),
    planeRange: document.getElementById("planeRange"),
    sourceMatrix: document.getElementById("sourceMatrix"),
    dctMatrix: document.getElementById("dctMatrix"),
    quantMatrix: document.getElementById("quantMatrix"),
    reconMatrix: document.getElementById("reconMatrix"),
    sourceBlockMean: document.getElementById("sourceBlockMean"),
    dctPeak: document.getElementById("dctPeak"),
    nonzeroStage: document.getElementById("nonzeroStage"),
    reconError: document.getElementById("reconError"),
    selectionChip: document.getElementById("selectionChip"),
    resetCoeffBtn: document.getElementById("resetCoeffBtn"),
    basisGrid: document.getElementById("basisGrid"),
    basisDetailCanvas: document.getElementById("basisDetailCanvas"),
    basisDetailTitle: document.getElementById("basisDetailTitle"),
    basisFreqChip: document.getElementById("basisFreqChip"),
    basisGValue: document.getElementById("basisGValue"),
    basisShare: document.getElementById("basisShare"),
    basisQValue: document.getElementById("basisQValue"),
    basisNote: document.getElementById("basisNote"),
    lumaQualityRange: document.getElementById("lumaQualityRange"),
    chromaQualityRange: document.getElementById("chromaQualityRange"),
    lumaQuality: document.getElementById("lumaQuality"),
    chromaQuality: document.getElementById("chromaQuality"),
    lumaScale: document.getElementById("lumaScale"),
    chromaScale: document.getElementById("chromaScale"),
    csvControls: document.querySelectorAll("[data-table-control]"),
    quantStateBadge: document.getElementById("quantStateBadge"),
    unitStepBtn: document.getElementById("unitStepBtn"),
    standardBtn: document.getElementById("standardBtn"),
    tableEditor: document.getElementById("tableEditor"),
    tableTabs: document.querySelector(".table-tabs"),
    quantTableGrid: document.getElementById("quantTableGrid"),
    editBadge: document.getElementById("editBadge"),
    nonzeroCount: document.getElementById("nonzeroCount"),
    nonzeroPercent: document.getElementById("nonzeroPercent"),
    nonzeroMeter: document.getElementById("nonzeroMeter"),
    retainedEnergy: document.getElementById("retainedEnergy"),
    selectedMae: document.getElementById("selectedMae"),
    selectedMax: document.getElementById("selectedMax"),
    globalPsnr: document.getElementById("globalPsnr"),
    boundaryError: document.getElementById("boundaryError"),
    boundaryGlobal: document.getElementById("boundaryGlobal"),
    boundaryScale: document.querySelector(".boundary-scale"),
    energyBars: document.getElementById("energyBars"),
    highFreqShare: document.getElementById("highFreqShare"),
    q1Mae: document.getElementById("q1Mae"),
    q1Max: document.getElementById("q1Max"),
    q1Changed: document.getElementById("q1Changed"),
    q1ResultNote: document.getElementById("q1ResultNote"),
    footerQuality: document.getElementById("footerQuality"),
    toast: document.getElementById("toast"),
    planeThumbs: {
      Y: document.getElementById("planeThumbY"),
      Cb: document.getElementById("planeThumbCb"),
      Cr: document.getElementById("planeThumbCr"),
    },
  };

  let rebuildFrame = 0;
  let toastTimer = 0;

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function tableForPlane(plane) {
    return plane === "Y" ? state.lumaTable : state.chromaTable;
  }

  function activeTableKind() {
    return state.plane === "Y" ? "luma" : "chroma";
  }

  function blockKey(plane, blockX, blockY) {
    return `${plane}:${blockX}:${blockY}`;
  }

  function currentBlockKey() {
    return blockKey(state.plane, state.blockX, state.blockY);
  }

  function extractBlock(planeData, blockX, blockY) {
    const values = new Uint8Array(BLOCK * BLOCK);
    for (let y = 0; y < BLOCK; y += 1) {
      for (let x = 0; x < BLOCK; x += 1) {
        values[y * BLOCK + x] =
          planeData[(blockY * BLOCK + y) * WIDTH + blockX * BLOCK + x];
      }
    }
    return values;
  }

  function formatValue(value, digits) {
    if (!Number.isFinite(value)) return "∞";
    const places = digits === undefined ? 2 : digits;
    return value.toFixed(places);
  }

  function formatCoefficient(value) {
    if (Math.abs(value) < 0.05) return "0";
    if (Math.abs(value) >= 100) return Math.round(value).toString();
    return value.toFixed(1);
  }

  function clearsEdits() {
    state.overrides.clear();
  }

  function rebuildPlane() {
    const planeData = state.sample.planes[state.plane];
    const table = tableForPlane(state.plane);
    const reconstructedPlane = new Uint8ClampedArray(WIDTH * HEIGHT);
    let current = null;
    let q1ErrorSum = 0;
    let q1Max = 0;
    let q1Changed = 0;

    for (let blockY = 0; blockY < BLOCKS_Y; blockY += 1) {
      for (let blockX = 0; blockX < BLOCKS_X; blockX += 1) {
        const values = extractBlock(planeData, blockX, blockY);
        const coeffs = M.fdctBlock(values, 128);
        const rawQuantized = M.quantize(coeffs, table);
        const key = blockKey(state.plane, blockX, blockY);
        const quantized = state.overrides.has(key)
          ? new Int32Array(state.overrides.get(key))
          : rawQuantized;
        const dequantized = M.dequantize(quantized, table);
        const spatial = M.idctBlock(dequantized, 128);
        const reconstructed = M.roundPixels(spatial);
        const q1 = M.reconstructBlock(values, UNIT_TABLE);
        q1ErrorSum += q1.error.mae * values.length;
        q1Max = Math.max(q1Max, q1.error.max);
        q1Changed += q1.error.changed;

        for (let y = 0; y < BLOCK; y += 1) {
          for (let x = 0; x < BLOCK; x += 1) {
            reconstructedPlane[(blockY * BLOCK + y) * WIDTH + blockX * BLOCK + x] =
              reconstructed[y * BLOCK + x];
          }
        }

        if (blockX === state.blockX && blockY === state.blockY) {
          current = {
            values,
            coeffs,
            rawQuantized,
            quantized,
            dequantized,
            spatial,
            reconstructed,
            error: M.metricsBetween(values, reconstructed),
            q1,
            table,
            band: M.bandEnergy(coeffs, dequantized),
            plane: state.plane,
            blockX,
            blockY,
            manuallyEdited: state.overrides.has(key),
          };
        }
      }
    }

    state.planeRecon = reconstructedPlane;
    state.planeMetrics = {
      psnr: M.planePsnr(planeData, reconstructedPlane),
      boundary: M.boundaryError(planeData, reconstructedPlane, WIDTH, HEIGHT, BLOCK),
      blockBoundary: M.blockBoundaryError(
        planeData,
        reconstructedPlane,
        WIDTH,
        HEIGHT,
        state.blockX,
        state.blockY,
        BLOCK,
      ),
    };
    state.q1PlaneMetrics = {
      mae: q1ErrorSum / (WIDTH * HEIGHT),
      max: q1Max,
      changed: q1Changed,
      pixels: WIDTH * HEIGHT,
    };
    state.current = current;
    render();
  }

  function scheduleRebuild() {
    if (rebuildFrame) cancelAnimationFrame(rebuildFrame);
    rebuildFrame = requestAnimationFrame(() => {
      rebuildFrame = 0;
      rebuildPlane();
    });
  }

  function createPlaneSource(planeData) {
    const canvas = document.createElement("canvas");
    canvas.width = WIDTH;
    canvas.height = HEIGHT;
    const context = canvas.getContext("2d");
    const image = context.createImageData(WIDTH, HEIGHT);
    for (let i = 0; i < planeData.length; i += 1) {
      const value = planeData[i];
      image.data[i * 4] = value;
      image.data[i * 4 + 1] = value;
      image.data[i * 4 + 2] = value;
      image.data[i * 4 + 3] = 255;
    }
    context.putImageData(image, 0, 0);
    return canvas;
  }

  function drawSourcePreview() {
    const canvas = dom.sourceCanvas;
    const context = canvas.getContext("2d");
    const planeData = state.sample.planes[state.plane];
    const source = createPlaneSource(planeData);
    const scale = canvas.width / WIDTH;
    const blockSize = BLOCK * scale;

    context.imageSmoothingEnabled = false;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(source, 0, 0, canvas.width, canvas.height);

    context.strokeStyle = "rgba(15, 31, 25, 0.25)";
    context.lineWidth = 0.8;
    context.beginPath();
    for (let x = BLOCK; x < WIDTH; x += BLOCK) {
      context.moveTo(x * scale + 0.5, 0);
      context.lineTo(x * scale + 0.5, canvas.height);
    }
    for (let y = BLOCK; y < HEIGHT; y += BLOCK) {
      context.moveTo(0, y * scale + 0.5);
      context.lineTo(canvas.width, y * scale + 0.5);
    }
    context.stroke();

    context.fillStyle = "rgba(221, 91, 50, 0.10)";
    context.fillRect(
      state.blockX * blockSize,
      state.blockY * blockSize,
      blockSize,
      blockSize,
    );
    context.strokeStyle = "#f05c32";
    context.lineWidth = 2.5;
    context.strokeRect(
      state.blockX * blockSize + 1.25,
      state.blockY * blockSize + 1.25,
      blockSize - 2.5,
      blockSize - 2.5,
    );
    context.strokeStyle = "rgba(255, 248, 236, 0.55)";
    context.lineWidth = 0.8;
    context.strokeRect(
      state.blockX * blockSize + 3,
      state.blockY * blockSize + 3,
      blockSize - 6,
      blockSize - 6,
    );
  }

  function drawPlaneThumbs() {
    Object.entries(dom.planeThumbs).forEach(([plane, canvas]) => {
      const context = canvas.getContext("2d");
      const source = createPlaneSource(state.sample.planes[plane]);
      context.imageSmoothingEnabled = false;
      context.clearRect(0, 0, canvas.width, canvas.height);
      context.drawImage(source, 0, 0, canvas.width, canvas.height);
    });
  }

  function renderSource() {
    const planeData = state.sample.planes[state.plane];
    let min = 255;
    let max = 0;
    let sum = 0;
    for (let i = 0; i < planeData.length; i += 1) {
      min = Math.min(min, planeData[i]);
      max = Math.max(max, planeData[i]);
      sum += planeData[i];
    }

    drawSourcePreview();
    drawPlaneThumbs();
    dom.sourceBlockLabel.textContent = `${String(state.blockY).padStart(2, "0")},${String(
      state.blockX,
    ).padStart(2, "0")}`;
    dom.blockNavLabel.textContent = `(${state.blockY}, ${state.blockX})`;
    dom.planeMean.textContent = formatValue(sum / planeData.length, 1);
    dom.planeRange.textContent = `${min} – ${max}`;
    dom.toolbarPlane.textContent =
      state.plane === "Y" ? "亮度平面 Y" : `色度平面 ${state.plane}`;
    dom.selectionChip.textContent = `${state.plane} · 块 (${state.blockY},${state.blockX})`;

    document.querySelectorAll(".plane-tab").forEach((tab) => {
      const active = tab.dataset.plane === state.plane;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", String(active));
    });
  }

  function mixColor(base, target, amount) {
    const alpha = clamp(amount, 0, 1);
    return [
      Math.round(base[0] + (target[0] - base[0]) * alpha),
      Math.round(base[1] + (target[1] - base[1]) * alpha),
      Math.round(base[2] + (target[2] - base[2]) * alpha),
    ];
  }

  function dctColor(value, maxAbs) {
    const neutral = [244, 240, 229];
    const positive = [213, 96, 55];
    const negative = [43, 117, 137];
    const normalized = maxAbs > 0 ? Math.min(1, Math.abs(value) / maxAbs) : 0;
    const amount = 0.07 + 0.86 * Math.pow(normalized, 0.62);
    const color = mixColor(neutral, value >= 0 ? positive : negative, amount);
    return `rgb(${color[0]}, ${color[1]}, ${color[2]})`;
  }

  function quantColor(value) {
    if (value === 0) return "#fbf7ed";
    const neutral = [250, 246, 235];
    const positive = [245, 212, 195];
    const negative = [212, 235, 230];
    const normalized = Math.min(1, Math.abs(value) / 12);
    const color = mixColor(neutral, value > 0 ? positive : negative, 0.35 + normalized * 0.55);
    return `rgb(${color[0]}, ${color[1]}, ${color[2]})`;
  }

  function buildPixelCells(values, comparedValues) {
    let html = "";
    for (let y = 0; y < BLOCK; y += 1) {
      for (let x = 0; x < BLOCK; x += 1) {
        const index = y * BLOCK + x;
        const value = values[index];
        const dark = value < 102;
        const compared = comparedValues ? comparedValues[index] : value;
        const difference = comparedValues ? value - comparedValues[index] : 0;
        const title = comparedValues
          ? `重建 (${y},${x}) = ${value}；原值 ${comparedValues[index]}；误差 ${difference}`
          : `原图 (${y},${x}) = ${value}`;
        const errorClass = difference !== 0 ? " has-error" : "";
        html += `<div class="matrix-cell${dark ? " is-dark" : ""}${errorClass}" title="${title}" style="background:rgb(${value}, ${value}, ${value})">${value}</div>`;
      }
    }
    return html;
  }

  function renderStages() {
    const current = state.current;
    if (!current) return;

    dom.sourceMatrix.innerHTML = buildPixelCells(current.values, null);
    dom.reconMatrix.innerHTML = buildPixelCells(current.reconstructed, current.values);

    let peak = 0;
    let peakU = 0;
    let peakV = 0;
    for (let v = 0; v < BLOCK; v += 1) {
      for (let u = 0; u < BLOCK; u += 1) {
        const value = current.coeffs[v * BLOCK + u];
        if (Math.abs(value) > Math.abs(peak)) {
          peak = value;
          peakU = u;
          peakV = v;
        }
      }
    }

    let dctHtml = "";
    let quantHtml = "";
    let nonzero = 0;
    for (let v = 0; v < BLOCK; v += 1) {
      for (let u = 0; u < BLOCK; u += 1) {
        const index = v * BLOCK + u;
        const coefficient = current.coeffs[index];
        const quantized = current.quantized[index];
        if (quantized !== 0) nonzero += 1;

        dctHtml += `<div class="matrix-cell dct-cell" data-u="${u}" data-v="${v}" data-freq="${u},${v}" title="F(u=${u}, v=${v}) = ${formatValue(
          coefficient,
          3,
        )}" style="background:${dctColor(coefficient, Math.abs(peak))}">${formatCoefficient(
          coefficient,
        )}</div>`;

        quantHtml += `<div class="matrix-cell${quantized === 0 ? " is-zero" : ""}" style="background:${quantColor(
          quantized,
        )}" title="Q(u=${u}, v=${v}) = ${current.table[index]}"><input class="quant-cell" type="number" inputmode="numeric" step="1" aria-label="量化系数 u=${u}, v=${v}" data-index="${index}" value="${quantized}" /></div>`;
      }
    }
    dom.dctMatrix.innerHTML = dctHtml;
    dom.quantMatrix.innerHTML = quantHtml;

    let sourceSum = 0;
    for (let i = 0; i < current.values.length; i += 1) sourceSum += current.values[i];
    dom.sourceBlockMean.textContent = `μ ${formatValue(sourceSum / current.values.length, 1)}`;
    dom.dctPeak.textContent = `peak ${formatCoefficient(peak)} · (${peakU},${peakV})`;
    dom.nonzeroStage.textContent = `${nonzero} / 64`;
    dom.reconError.textContent = `MAE ${formatValue(current.error.mae, 2)}`;
  }

  function putBasisPixels(context, u, v, coefficient, strength, size) {
    const base = [247, 243, 233];
    const positive = [211, 96, 55];
    const negative = [43, 117, 137];
    const image = context.createImageData(BLOCK, BLOCK);

    for (let y = 0; y < BLOCK; y += 1) {
      for (let x = 0; x < BLOCK; x += 1) {
        const basis = M.basisContribution(v, u, x, y);
        const contribution = coefficient * basis;
        const pixel = (y * BLOCK + x) * 4;
        let color = base;
        if (coefficient !== 0 && Math.abs(contribution) > 0.00001) {
          const target = contribution > 0 ? positive : negative;
          color = mixColor(base, target, 0.18 + strength * 0.82);
        } else if (coefficient === 0 && Math.abs(coefficient) < 0.0001) {
          color = mixColor(base, [231, 226, 214], 0.55);
        }
        image.data[pixel] = color[0];
        image.data[pixel + 1] = color[1];
        image.data[pixel + 2] = color[2];
        image.data[pixel + 3] = 255;
      }
    }

    const source = document.createElement("canvas");
    source.width = BLOCK;
    source.height = BLOCK;
    source.getContext("2d").putImageData(image, 0, 0);
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";
    context.clearRect(0, 0, size, size);
    context.drawImage(source, 0, 0, size, size);
  }

  function drawBasisTile(canvas, u, v, coefficient, maxAbs) {
    const context = canvas.getContext("2d");
    const relative = maxAbs > 0 ? Math.abs(coefficient) / maxAbs : 0;
    const strength = 0.18 + Math.sqrt(relative) * 0.82;
    putBasisPixels(context, u, v, coefficient, strength, canvas.width);
  }

  function frequencyClass(u, v) {
    const radius = Math.max(u, v);
    if (radius === 0) return "DC";
    if (radius <= 2) return "低频";
    if (radius <= 5) return "中频";
    return "高频";
  }

  function frequencyNote(u, v, coefficient) {
    if (coefficient === 0) return "该频率的量化系数为 0，当前不产生空间贡献。";
    if (u === 0 && v === 0) return "DC 项决定该块的平均亮度；修改它会整体抬升或压低重建块。";
    if (v === 0) return "仅沿水平方向变化，产生纵向条纹状的余弦贡献。";
    if (u === 0) return "仅沿垂直方向变化，产生横向条纹状的余弦贡献。";
    if (Math.max(u, v) >= 6) return "该高频项对应细密棋盘纹理；较大的量化步长通常会把它压成 0。";
    return "二维余弦基函数；u 控水平变化，v 控垂直变化。";
  }

  function renderBasis() {
    const current = state.current;
    if (!current) return;
    let maxAbs = 0;
    for (let i = 0; i < current.dequantized.length; i += 1) {
      maxAbs = Math.max(maxAbs, Math.abs(current.dequantized[i]));
    }

    let html = "";
    for (let v = 0; v < BLOCK; v += 1) {
      for (let u = 0; u < BLOCK; u += 1) {
        const coefficient = current.dequantized[v * BLOCK + u];
        const active =
          state.selectedBasis.u === u && state.selectedBasis.v === v ? " is-active" : "";
        html += `<button class="basis-cell${active}" type="button" data-u="${u}" data-v="${v}" title="F(u=${u}, v=${v}) 的基函数；去量化贡献 G=${formatValue(
          coefficient,
          2,
        )}"><canvas width="36" height="36" aria-hidden="true"></canvas><span class="basis-cell-index">${u},${v}</span></button>`;
      }
    }
    dom.basisGrid.innerHTML = html;

    dom.basisGrid.querySelectorAll(".basis-cell").forEach((cell) => {
      const u = Number(cell.dataset.u);
      const v = Number(cell.dataset.v);
      const canvas = cell.querySelector("canvas");
      const coefficient = current.dequantized[v * BLOCK + u];
      drawBasisTile(canvas, u, v, coefficient, maxAbs);
    });

    const { u, v } = state.selectedBasis;
    const coefficient = current.dequantized[v * BLOCK + u];
    const share =
      current.band.retainedTotal > 0
        ? (coefficient * coefficient) / current.band.retainedTotal
        : 0;
    const detailCanvas = dom.basisDetailCanvas;
    putBasisPixels(
      detailCanvas.getContext("2d"),
      u,
      v,
      coefficient,
      coefficient === 0 ? 0.04 : 0.95,
      detailCanvas.width,
    );

    dom.basisDetailTitle.textContent = `F(${u},${v})`;
    dom.basisFreqChip.textContent = frequencyClass(u, v);
    dom.basisGValue.textContent = formatValue(coefficient, 2);
    dom.basisShare.textContent = `${formatValue(share * 100, 2)}%`;
    dom.basisQValue.textContent = current.table[v * BLOCK + u];
    dom.basisNote.textContent = frequencyNote(u, v, coefficient);
  }

  function qualityScaleLabel(quality, custom) {
    if (custom) return "自定义表";
    const scale = M.qualityScale(quality);
    return scale === 0 ? "步长 = 1" : `表倍率 ×${(scale / 100).toFixed(2)}`;
  }

  function renderQualityControls() {
    const lumaCustom = state.customLuma;
    const chromaCustom = state.customChroma;
    dom.lumaQuality.textContent = lumaCustom ? "—" : String(state.lumaQuality);
    dom.chromaQuality.textContent = chromaCustom ? "—" : String(state.chromaQuality);
    dom.lumaQualityRange.value = String(state.lumaQuality);
    dom.chromaQualityRange.value = String(state.chromaQuality);
    dom.lumaScale.textContent = qualityScaleLabel(state.lumaQuality, lumaCustom);
    dom.chromaScale.textContent = qualityScaleLabel(state.chromaQuality, chromaCustom);

    document.querySelectorAll("[data-table-control]").forEach((control) => {
      const kind = control.dataset.tableControl;
      const custom = kind === "luma" ? lumaCustom : chromaCustom;
      control.classList.toggle("is-custom", custom);
      const metaLabel = control.querySelector(".quality-meta span:first-child");
      metaLabel.textContent =
        kind === "luma"
          ? custom
            ? "自定义亮度表"
            : "标准亮度表"
          : custom
            ? "自定义色度表"
            : "共用色度表";
    });

    if (lumaCustom || chromaCustom) {
      dom.quantStateBadge.textContent = "自定义";
    } else if (state.lumaQuality === state.chromaQuality) {
      dom.quantStateBadge.textContent = `JPEG ${state.lumaQuality}`;
    } else {
      dom.quantStateBadge.textContent = `Y${state.lumaQuality} · C${state.chromaQuality}`;
    }

    dom.footerQuality.textContent =
      lumaCustom || chromaCustom
        ? "自定义量化表"
        : `Y ${state.lumaQuality} · C ${state.chromaQuality}`;

    const table = state.tableTab === "luma" ? state.lumaTable : state.chromaTable;
    let html = "";
    table.forEach((value, index) => {
      const u = index % BLOCK;
      const v = Math.floor(index / BLOCK);
      html += `<input class="quant-table-input" type="number" inputmode="numeric" min="1" max="255" step="1" aria-label="u=${u}, v=${v} 量化步长" data-table-index="${index}" value="${value}" />`;
    });
    dom.quantTableGrid.innerHTML = html;
    document.querySelectorAll(".table-tab").forEach((tab) => {
      tab.classList.toggle("is-active", tab.dataset.tableTab === state.tableTab);
    });
  }

  function renderMetrics() {
    const current = state.current;
    const metrics = state.planeMetrics;
    if (!current || !metrics) return;

    let nonzero = 0;
    current.quantized.forEach((value) => {
      if (value !== 0) nonzero += 1;
    });
    const nonzeroShare = nonzero / 64;
    dom.nonzeroCount.textContent = String(nonzero);
    dom.nonzeroPercent.textContent = `${Math.round(nonzeroShare * 100)}%`;
    dom.nonzeroMeter.style.width = `${nonzeroShare * 100}%`;
    dom.retainedEnergy.textContent = `${formatValue(
      clamp(current.band.retainedRatio, 0, 1) * 100,
      1,
    )}%`;
    dom.selectedMae.textContent = formatValue(current.error.mae, 3);
    dom.selectedMax.textContent = String(current.error.max);
    dom.globalPsnr.textContent = Number.isFinite(metrics.psnr)
      ? `${formatValue(metrics.psnr, 2)} dB`
      : "∞ dB";
    dom.boundaryError.textContent = formatValue(metrics.blockBoundary.mae, 3);
    dom.boundaryGlobal.textContent = formatValue(metrics.boundary.mae, 3);
    dom.boundaryError.title = `当前块 ${metrics.blockBoundary.count} 条边界样本，最大边界误差 ${formatValue(
      metrics.blockBoundary.max,
      2,
    )}`;

    const lit = metrics.blockBoundary.mae === 0
      ? 0
      : clamp(Math.ceil(metrics.blockBoundary.mae), 1, 5);
    dom.boundaryScale.querySelectorAll("span").forEach((segment, index) => {
      segment.classList.toggle("is-lit", index < lit);
    });

    dom.editBadge.textContent = current.manuallyEdited ? "手改" : "自动";
    dom.editBadge.style.color = current.manuallyEdited ? "var(--orange-dark)" : "";
    dom.editBadge.style.background = current.manuallyEdited ? "var(--orange-soft)" : "";
    dom.editBadge.style.borderColor = current.manuallyEdited ? "#eab7a0" : "";

    const q1 = state.q1PlaneMetrics;
    dom.q1Mae.textContent = formatValue(q1.mae, 3);
    dom.q1Max.textContent = String(q1.max);
    dom.q1Changed.textContent = `${q1.changed} / ${q1.pixels}`;
    dom.q1ResultNote.textContent =
      q1.changed === 0
        ? "本平面样本恰好可从取整后的整数系数重建；这不表示变换或编码路径无损。"
        : `${q1.changed} 个像素因 DCT 系数的整数取整与 8-bit 像素舍入而改变。`;
  }

  function renderEnergy() {
    const current = state.current;
    if (!current) return;
    let html = "";
    current.band.bands.forEach((band) => {
      const rawPercent = band.rawShare * 100;
      const retainedPercent = band.retainedShare * 100;
      html += `<div class="energy-row" title="${band.label}：原始 ${formatValue(
        rawPercent,
        2,
      )}% · 量化后保留 ${formatValue(retainedPercent, 2)}%">
        <span class="energy-name">${band.label}<em>r = ${band.range}</em></span>
        <div class="energy-track">
          <i class="energy-bar-raw" style="width:${clamp(rawPercent, 0, 100)}%"></i>
          <i class="energy-bar-retained" style="width:${clamp(retainedPercent, 0, 100)}%"></i>
        </div>
        <span class="energy-value">${formatValue(rawPercent, 1)}%</span>
      </div>`;
    });
    dom.energyBars.innerHTML = html;
    dom.highFreqShare.textContent = `${formatValue(current.band.highRawShare * 100, 1)}%`;
  }

  function render() {
    renderSource();
    renderStages();
    renderBasis();
    renderQualityControls();
    renderMetrics();
    renderEnergy();
  }

  function showToast(message) {
    window.clearTimeout(toastTimer);
    dom.toast.textContent = message;
    dom.toast.classList.add("is-visible");
    toastTimer = window.setTimeout(() => {
      dom.toast.classList.remove("is-visible");
    }, 2300);
  }

  function selectBlock(blockX, blockY) {
    const nextX = clamp(blockX, 0, BLOCKS_X - 1);
    const nextY = clamp(blockY, 0, BLOCKS_Y - 1);
    if (nextX !== state.blockX || nextY !== state.blockY) {
      clearsEdits();
    }
    state.blockX = nextX;
    state.blockY = nextY;
    rebuildPlane();
  }

  function selectPlane(plane) {
    if (!["Y", "Cb", "Cr"].includes(plane) || plane === state.plane) return;
    state.plane = plane;
    clearsEdits();
    rebuildPlane();
  }

  function selectPattern(pattern) {
    if (!["flat", "diagonal", "checker", "color"].includes(pattern)) return;
    state.pattern = pattern;
    state.sample = P.createSample(pattern);
    clearsEdits();
    rebuildPlane();
  }

  function setQuality(kind, quality) {
    const value = clamp(Math.round(Number(quality)), 1, 100);
    if (kind === "luma") {
      state.lumaQuality = value;
      state.lumaTable = M.makeQuantTable("luma", value);
      state.customLuma = false;
    } else {
      state.chromaQuality = value;
      state.chromaTable = M.makeQuantTable("chroma", value);
      state.customChroma = false;
    }
    clearsEdits();
    scheduleRebuild();
  }

  function editTableValue(kind, index, rawValue) {
    const value = Math.round(Number(rawValue));
    if (!Number.isFinite(value)) return;
    const clamped = clamp(value, 1, 255);
    if (kind === "luma") {
      state.lumaTable[index] = clamped;
      state.customLuma = true;
    } else {
      state.chromaTable[index] = clamped;
      state.customChroma = true;
    }
    clearsEdits();
    rebuildPlane();
    showToast(
      `已更新${kind === "luma" ? "亮度" : "色度"}表 Q(${index % BLOCK},${Math.floor(
        index / BLOCK,
      )}) = ${clamped}`,
    );
  }

  function editQuantizedCoefficient(index, rawValue) {
    const value = Math.round(Number(rawValue));
    if (!Number.isFinite(value) || !state.current) return;
    const clamped = clamp(value, -2048, 2047);
    const quantized = new Int32Array(state.current.quantized);
    quantized[index] = clamped;
    state.overrides.set(currentBlockKey(), quantized);
    rebuildPlane();
  }

  function bindEvents() {
    dom.patternButtons.addEventListener("click", (event) => {
      const button = event.target.closest("[data-pattern]");
      if (!button) return;
      selectPattern(button.dataset.pattern);
      dom.patternButtons.querySelectorAll("[data-pattern]").forEach((item) => {
        item.classList.toggle("is-active", item === button);
      });
    });

    dom.planeTabs.addEventListener("click", (event) => {
      const tab = event.target.closest("[data-plane]");
      if (tab) selectPlane(tab.dataset.plane);
    });

    dom.sourceCanvas.tabIndex = 0;
    dom.sourceCanvas.addEventListener("click", (event) => {
      const rect = dom.sourceCanvas.getBoundingClientRect();
      const x = ((event.clientX - rect.left) / rect.width) * WIDTH;
      const y = ((event.clientY - rect.top) / rect.height) * HEIGHT;
      selectBlock(Math.floor(x / BLOCK), Math.floor(y / BLOCK));
    });
    dom.sourceCanvas.addEventListener("keydown", (event) => {
      const moves = {
        ArrowLeft: [-1, 0],
        ArrowRight: [1, 0],
        ArrowUp: [0, -1],
        ArrowDown: [0, 1],
      };
      if (!moves[event.key]) return;
      event.preventDefault();
      selectBlock(state.blockX + moves[event.key][0], state.blockY + moves[event.key][1]);
    });

    dom.blockNav.addEventListener("click", (event) => {
      const button = event.target.closest("[data-move]");
      if (!button) return;
      const moves = {
        left: [-1, 0],
        right: [1, 0],
        up: [0, -1],
        down: [0, 1],
      };
      const [dx, dy] = moves[button.dataset.move];
      selectBlock(state.blockX + dx, state.blockY + dy);
    });

    dom.lumaQualityRange.addEventListener("input", (event) => {
      setQuality("luma", event.target.value);
    });
    dom.chromaQualityRange.addEventListener("input", (event) => {
      setQuality("chroma", event.target.value);
    });

    dom.unitStepBtn.addEventListener("click", () => {
      state.lumaQuality = 100;
      state.chromaQuality = 100;
      state.lumaTable = M.makeUnitTable();
      state.chromaTable = M.makeUnitTable();
      state.customLuma = false;
      state.customChroma = false;
      clearsEdits();
      rebuildPlane();
      showToast("两张量化表都已设为步长 1；重建仍会报告舍入误差。");
    });

    dom.standardBtn.addEventListener("click", () => {
      state.lumaQuality = 75;
      state.chromaQuality = 75;
      state.lumaTable = M.makeQuantTable("luma", 75);
      state.chromaTable = M.makeQuantTable("chroma", 75);
      state.customLuma = false;
      state.customChroma = false;
      clearsEdits();
      rebuildPlane();
      showToast("已恢复 JPEG 标准 75 的亮度与色度表。");
    });

    dom.tableTabs.addEventListener("click", (event) => {
      const tab = event.target.closest("[data-table-tab]");
      if (!tab) return;
      state.tableTab = tab.dataset.tableTab;
      renderQualityControls();
    });

    dom.quantTableGrid.addEventListener("change", (event) => {
      const input = event.target.closest("[data-table-index]");
      if (!input) return;
      editTableValue(state.tableTab, Number(input.dataset.tableIndex), input.value);
    });
    dom.quantTableGrid.addEventListener("focusin", (event) => {
      if (event.target.matches(".quant-table-input")) event.target.select();
    });

    dom.basisGrid.addEventListener("click", (event) => {
      const cell = event.target.closest("[data-u][data-v]");
      if (!cell) return;
      state.selectedBasis = { u: Number(cell.dataset.u), v: Number(cell.dataset.v) };
      renderBasis();
    });

    dom.dctMatrix.addEventListener("click", (event) => {
      const cell = event.target.closest("[data-u][data-v]");
      if (!cell) return;
      state.selectedBasis = { u: Number(cell.dataset.u), v: Number(cell.dataset.v) };
      renderBasis();
    });

    dom.quantMatrix.addEventListener("change", (event) => {
      const input = event.target.closest("[data-index]");
      if (!input) return;
      editQuantizedCoefficient(Number(input.dataset.index), input.value);
    });
    dom.quantMatrix.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && event.target.matches(".quant-cell")) {
        event.preventDefault();
        event.target.blur();
      }
    });
    dom.quantMatrix.addEventListener("focusin", (event) => {
      if (event.target.matches(".quant-cell")) event.target.select();
    });

    dom.resetCoeffBtn.addEventListener("click", () => {
      const key = currentBlockKey();
      if (!state.overrides.has(key)) {
        showToast("当前块没有手动编辑的量化系数。");
        return;
      }
      state.overrides.delete(key);
      rebuildPlane();
      showToast("已恢复当前块由量化表计算出的系数。");
    });
  }

  bindEvents();
  rebuildPlane();
})();
