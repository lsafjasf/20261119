import {
  applyEdit,
  bytesToHex,
  hex,
  offsetLabel,
  parseJpeg,
  planEdit,
  TARGET_TAGS,
} from "./exif.js";

const $ = (id) => document.getElementById(id);
const els = {
  sampleList: $("sample-list"),
  sampleCount: $("sample-count"),
  dropzone: $("dropzone"),
  fileInput: $("file-input"),
  emptyState: $("empty-state"),
  inspectContent: $("inspect-content"),
  inspectPanel: $("inspect-panel"),
  preview: $("asset-preview"),
  previewSize: $("preview-size"),
  previewSeal: $("preview-seal"),
  assetId: $("asset-id"),
  assetKind: $("asset-kind"),
  assetName: $("asset-name"),
  assetNote: $("asset-note"),
  statSize: $("stat-size"),
  statFrame: $("stat-frame"),
  statEndian: $("stat-endian"),
  statIfds: $("stat-ifds"),
  scanHash: $("scan-hash"),
  mapMid: $("map-mid"),
  mapEnd: $("map-end"),
  fileMap: $("file-map"),
  fileMapCaption: $("file-map-caption"),
  tiffMap: $("tiff-map"),
  tiffMapSize: $("tiff-map-size"),
  targetRanges: $("target-ranges"),
  refCount: $("ref-count"),
  refRail: $("ref-rail"),
  ifdTabs: $("ifd-tabs"),
  ifdContent: $("ifd-content"),
  editFields: $("edit-fields"),
  editForm: $("edit-form"),
  editPanel: document.querySelector(".edit-panel"),
  dateInput: $("date-input"),
  dateHelp: $("date-help"),
  orientationGrid: $("orientation-grid"),
  orientationNote: $("orientation-note"),
  preflightStatus: $("preflight-status"),
  preflightBody: $("preflight-body"),
  writeButton: $("write-button"),
  writeButtonLabel: $("write-button-label"),
  quickDownload: $("quick-download"),
  resultCard: $("result-card"),
  verifyPill: $("verify-pill"),
  verificationStrip: $("verification-strip"),
  scanCompare: $("scan-compare"),
  changeBody: $("change-body"),
  downloadButton: $("download-button"),
  resultFootnote: $("result-footnote"),
  toast: $("toast"),
};

const state = {
  samples: [],
  sampleId: null,
  meta: null,
  fileName: "",
  bytes: null,
  parsed: null,
  plan: null,
  result: null,
  activeIfdId: null,
  dateText: "",
  orientation: 1,
  previewUrl: null,
  toastTimer: null,
};

const ORIENTATION_LABELS = {
  1: "正常方向",
  2: "水平镜像",
  3: "旋转 180°",
  4: "垂直镜像",
  5: "转置",
  6: "顺时针 90°",
  7: "反转置",
  8: "逆时针 90°",
};

const STRUCTURAL_ERRORS = new Set([
  "not-jpeg", "marker-desync", "truncated-marker", "truncated-segment",
  "segment-oob", "missing-scan", "missing-exif", "tiff-truncated",
  "bad-byte-order", "bad-tiff-magic", "ifd0-oob", "ifd-oob",
  "ifd-table-oob", "data-oob", "ifd-pointer-oob", "next-ifd-oob",
  "unknown-type",
]);

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const formatCount = (value) => Number(value || 0).toLocaleString("zh-CN");
const formatBytes = (value) => {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(2)} MB`;
};

function showToast(message, type = "normal") {
  clearTimeout(state.toastTimer);
  els.toast.textContent = message;
  els.toast.className = `toast show${type === "error" ? " error" : ""}`;
  state.toastTimer = setTimeout(() => {
    els.toast.className = "toast";
  }, 3200);
}

function clearToast() {
  clearTimeout(state.toastTimer);
  els.toast.className = "toast";
}

async function initialize() {
  buildOrientationGrid();
  bindEvents();
  try {
    const response = await fetch("/samples/index.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.samples = await response.json();
    els.sampleCount.textContent = String(state.samples.length).padStart(2, "0");
    renderSampleList();
    if (state.samples[0]) await loadSample(state.samples[0].id);
  } catch (error) {
    els.sampleList.innerHTML = '<div class="loading-row">样本载入失败，可拖入本地 JPEG。</div>';
    showToast(`样本载入失败：${error.message}`, "error");
  }
}

function buildOrientationGrid() {
  els.orientationGrid.innerHTML = Array.from({ length: 8 }, (_, index) => {
    const value = index + 1;
    return `
      <button class="orientation-button" type="button" data-orientation="${value}" title="${ORIENTATION_LABELS[value]}">
        <span class="orientation-mini"></span>
        <strong>${value}</strong>
      </button>`;
  }).join("");
  selectOrientation(1, false);
}

function bindEvents() {
  els.sampleList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-sample-id]");
    if (button) loadSample(button.dataset.sampleId);
  });

  els.ifdTabs.addEventListener("click", (event) => {
    const button = event.target.closest("[data-ifd-id]");
    if (!button) return;
    state.activeIfdId = button.dataset.ifdId;
    renderIfdTabs();
    renderIfdContent();
  });

  els.orientationGrid.addEventListener("click", (event) => {
    const button = event.target.closest("[data-orientation]");
    if (!button) return;
    selectOrientation(Number(button.dataset.orientation));
    updatePlan();
  });

  els.dateInput.addEventListener("input", () => {
    state.dateText = els.dateInput.value;
    updatePlan();
  });

  els.editForm.addEventListener("submit", (event) => {
    event.preventDefault();
    runPatch();
  });

  els.downloadButton.addEventListener("click", downloadResult);
  els.quickDownload.addEventListener("click", downloadResult);
  els.fileInput.addEventListener("change", () => {
    if (els.fileInput.files?.[0]) loadFile(els.fileInput.files[0]);
  });

  for (const eventName of ["dragenter", "dragover"]) {
    els.dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      els.dropzone.classList.add("is-dragging");
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    els.dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      els.dropzone.classList.remove("is-dragging");
    });
  }
  els.dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (file) loadFile(file);
  });
}

function selectOrientation(value, update = true) {
  state.orientation = value;
  for (const button of els.orientationGrid.querySelectorAll("[data-orientation]")) {
    button.classList.toggle("active", Number(button.dataset.orientation) === value);
  }
  els.orientationNote.textContent = `${value} · ${ORIENTATION_LABELS[value]}`;
  if (!update) return;
}

async function loadSample(id) {
  const sample = state.samples.find((item) => item.id === id);
  if (!sample) return;
  try {
    const response = await fetch(`/samples/${sample.fileName}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const bytes = new Uint8Array(await response.arrayBuffer());
    loadBytes(bytes, sample, sample.fileName);
  } catch (error) {
    showToast(`样本读取失败：${error.message}`, "error");
  }
}

async function loadFile(file) {
  if (!file) return;
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    loadBytes(bytes, null, file.name);
  } catch (error) {
    showToast(`文件读取失败：${error.message}`, "error");
  }
}

function loadBytes(bytes, meta, fileName) {
  clearToast();
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.bytes = bytes;
  state.meta = meta;
  state.sampleId = meta?.id || null;
  state.fileName = fileName;
  state.result = null;
  state.parsed = parseJpeg(bytes);
  els.fileInput.value = "";
  state.previewUrl = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));

  const dateEntry = state.parsed.exif?.entries.find((entry) =>
    entry.tag === TARGET_TAGS.date && entry.valid && entry.ascii);
  const orientationEntry = state.parsed.exif?.entries.find((entry) =>
    entry.tag === TARGET_TAGS.orientation && entry.valid && entry.numbers?.length);
  state.dateText = dateEntry?.ascii || "2024:01:01 00:00:00";
  state.orientation = orientationEntry?.numbers?.[0] || 1;
  if (state.orientation < 1 || state.orientation > 8) state.orientation = 1;
  state.activeIfdId = dateEntry?.ifdId || state.parsed.exif?.ifds?.[0]?.id || null;
  els.dateInput.value = state.dateText;
  selectOrientation(state.orientation, false);
  els.dateInput.classList.remove("is-invalid");
  els.editFields.disabled = !state.parsed.exif;
  updatePlan();
  renderAll();
  els.emptyState.hidden = true;
  els.inspectContent.hidden = false;
  els.inspectPanel.scrollTop = 0;
  els.editPanel.scrollTop = 0;
}

function renderSampleList() {
  els.sampleList.innerHTML = state.samples.map((sample, index) => `
    <button class="sample-item${state.sampleId === sample.id ? " active" : ""}"
      type="button" data-sample-id="${escapeHtml(sample.id)}" data-blocked="${Boolean(sample.quarantine)}">
      <span class="sample-index">${String(index + 1).padStart(2, "0")}</span>
      <span class="sample-copy">
        <strong>${escapeHtml(sample.name)}</strong>
        <small>${escapeHtml(sample.subtitle)}</small>
      </span>
      <span class="sample-status" title="${sample.quarantine ? "越界引用" : "样本"}"></span>
    </button>`).join("");
}

function updatePlan() {
  state.result = null;
  state.plan = state.parsed
    ? planEdit(state.parsed, { dateText: state.dateText, orientation: state.orientation })
    : null;
  const dateIssue = state.plan?.errors.find((issue) => issue.code === "invalid-date");
  els.dateInput.classList.toggle("is-invalid", Boolean(dateIssue));
  els.dateHelp.classList.toggle("error", Boolean(dateIssue));
  els.dateHelp.textContent = dateIssue
    ? dateIssue.error || dateIssue.message
    : "DateTimeOriginal · ASCII · 原位覆写";
  renderPreflight();
  renderByteMap();
  renderResult();
  renderPreviewState();
}

function renderAll() {
  renderSampleList();
  renderAsset();
  renderRefs();
  renderIfdTabs();
  renderIfdContent();
  renderPreflight();
  renderByteMap();
  renderResult();
  renderPreviewState();
}

function renderAsset() {
  const parsed = state.parsed;
  const exif = parsed?.exif;
  const frame = parsed?.frame;
  const sampleIndex = state.samples.findIndex((sample) => sample.id === state.sampleId);
  const sof = parsed?.segments.find((segment) => segment.name?.startsWith("SOF"));

  els.preview.src = state.previewUrl || "";
  els.previewSize.textContent = frame ? `${frame.width} × ${frame.height}` : "NO FRAME";
  els.assetId.textContent = state.meta
    ? `SAMPLE / ${String(sampleIndex + 1).padStart(2, "0")}`
    : "LOCAL FILE";
  els.assetKind.textContent = sof ? `${sof.name} ${sof.name === "SOF2" ? "progressive" : "baseline"}` : "JPEG";
  els.assetName.textContent = state.fileName || "未命名.jpg";
  els.assetNote.textContent = state.meta?.note || "本地素材，仅在当前浏览器会话内解析，不上传到服务器。";
  els.statSize.textContent = formatBytes(parsed?.fileSize || 0);
  els.statFrame.textContent = frame ? `${frame.width}×${frame.height}` : "—";
  els.statEndian.textContent = exif
    ? `${exif.byteOrder === "little" ? "II" : "MM"} / ${exif.byteOrder === "little" ? "LE" : "BE"}`
    : "—";
  els.statIfds.textContent = exif ? `${exif.ifds.length}` : "0";
  els.scanHash.textContent = parsed?.scan?.hash || "NO SCAN DATA";
  els.scanHash.title = parsed?.scan?.hash || "";
}

function renderPreviewState() {
  if (!state.parsed) return;
  if (state.result?.ok) {
    els.previewSeal.textContent = "VERIFIED";
    els.previewSeal.className = "preview-seal is-pass";
  } else if (!state.parsed.valid) {
    els.previewSeal.textContent = "QUARANTINE";
    els.previewSeal.className = "preview-seal";
  } else {
    els.previewSeal.textContent = "READ ONLY";
    els.previewSeal.className = "preview-seal";
  }
}

function renderRefs() {
  const exif = state.parsed?.exif;
  if (!exif) {
    els.refCount.textContent = "0 LINKS";
    els.refRail.innerHTML = '<div class="ref-empty">未发现 TIFF IFD 链。</div>';
    return;
  }
  const targetIds = new Set(exif.entries
    .filter((entry) => entry.tag === TARGET_TAGS.date || entry.tag === TARGET_TAGS.orientation)
    .map((entry) => entry.id));
  const references = exif.references.filter((reference) =>
    reference.kind !== "data" || !reference.valid || targetIds.has(reference.entryId));
  els.refCount.textContent = `${exif.references.length} LINKS`;
  if (!references.length) {
    els.refRail.innerHTML = '<div class="ref-empty">当前 IFD 没有外置引用。</div>';
    return;
  }
  els.refRail.innerHTML = references.slice(0, 18).map((reference) => {
    const target = reference.toOffset === null ? "—" : offsetLabel(reference.toOffset);
    const suffix = reference.valid ? target : `${target} · OOB`;
    return `
      <div class="ref-line ${reference.kind}${reference.valid ? "" : " invalid"}">
        <span class="ref-node"></span>
        <span class="ref-copy">
          <strong title="${escapeHtml(reference.label)}">${escapeHtml(reference.label)}</strong>
          <small class="${reference.valid ? "" : "bad"}">${suffix}</small>
        </span>
      </div>`;
  }).join("");
}

function renderIfdTabs() {
  const ifds = state.parsed?.exif?.ifds || [];
  if (!ifds.length) {
    els.ifdTabs.innerHTML = "";
    return;
  }
  if (!ifds.some((ifd) => ifd.id === state.activeIfdId)) {
    state.activeIfdId = ifds[0].id;
  }
  els.ifdTabs.innerHTML = ifds.map((ifd) => {
    const target = ifd.entries.some((entry) =>
      entry.tag === TARGET_TAGS.date || entry.tag === TARGET_TAGS.orientation);
    const invalid = !ifd.valid || ifd.entries.some((entry) => !entry.valid);
    return `<button class="ifd-tab${ifd.id === state.activeIfdId ? " active" : ""}"
      type="button" data-ifd-id="${ifd.id}">${escapeHtml(ifd.path)}${target ? " •" : ""}${invalid ? " !" : ""}</button>`;
  }).join("");
}

function renderIfdContent() {
  const exif = state.parsed?.exif;
  const ifd = exif?.ifds.find((item) => item.id === state.activeIfdId);
  if (!ifd) {
    els.ifdContent.innerHTML = '<div class="ref-empty" style="padding:18px 15px">未找到 TIFF 目录。</div>';
    return;
  }
  const references = new Map();
  for (const reference of exif.references) {
    if (!references.has(reference.entryId)) references.set(reference.entryId, []);
    references.get(reference.entryId).push(reference);
  }
  const summary = `
    <div class="ifd-summary">
      <span>目录 <code>${escapeHtml(ifd.path)}</code></span>
      <span>偏移 <code>${offsetLabel(ifd.offset)}</code></span>
      <span>${ifd.entryCount} entries</span>
      <span>next <code>${ifd.nextOffset ? offsetLabel(ifd.nextOffset) : "NULL"}</code></span>
      <span>${ifd.valid ? "结构有效" : "结构异常"}</span>
    </div>`;
  const rows = ifd.entries.map((entry) => {
    const isTarget = entry.tag === TARGET_TAGS.date || entry.tag === TARGET_TAGS.orientation;
    const classes = [
      isTarget ? "is-target" : "",
      entry.valid ? "" : "is-invalid",
    ].filter(Boolean).join(" ");
    const badges = [
      isTarget ? '<span class="badge target">TARGET</span>' : "",
      entry.duplicateInIfd ? '<span class="badge">DUP</span>' : "",
      entry.valid ? "" : '<span class="badge danger">OOB</span>',
    ].join("");
    let referenceCell;
    const entryRefs = references.get(entry.id) || [];
    const pointerRef = entryRefs.find((reference) => reference.kind === "pointer");
    if (pointerRef) {
      referenceCell = `<span class="${pointerRef.valid ? "offset-link" : "bad"}">→ IFD ${offsetLabel(pointerRef.toOffset)}</span>`;
    } else if (!entry.valid && entry.storage === "offset") {
      referenceCell = `<span class="bad">× ${offsetLabel(entry.dataFileOffset)}</span>`;
    } else if (entry.storage === "offset") {
      referenceCell = `<span class="offset-link">→ ${offsetLabel(entry.dataFileOffset)} · ${entry.dataSize} B</span>`;
    } else {
      referenceCell = `<span class="inline">inline · ${offsetLabel(entry.valueFieldFileOffset)}</span>`;
    }
    return `
      <tr class="${classes}">
        <td class="tag-cell"><code>0x${hex(entry.tag)}</code><strong>${escapeHtml(entry.tagLabel)}</strong>${badges}</td>
        <td>${escapeHtml(entry.typeName)}</td>
        <td class="mono">${entry.count}</td>
        <td>${entry.storage === "offset" ? "外置" : entry.storage === "inline" ? "内联" : "未知"}</td>
        <td class="value-cell" title="${escapeHtml(entry.valueText)}">${escapeHtml(entry.valueText || "—")}</td>
        <td class="ref-cell">${referenceCell}</td>
      </tr>`;
  }).join("");
  const alerts = (exif.issues || [])
    .filter((issue) => issue.ifdId === ifd.id || (!issue.ifdId && issue.severity === "error"))
    .slice(0, 3)
    .map((issue) => `<div class="ifd-alert"><strong>!</strong><span>${escapeHtml(issue.message)}</span></div>`)
    .join("");
  els.ifdContent.innerHTML = `
    ${summary}
    <div class="entry-table-wrap">
      <table class="entry-table">
        <colgroup>
          <col style="width:31%" /><col style="width:10%" /><col style="width:8%" />
          <col style="width:9%" /><col style="width:21%" /><col style="width:21%" />
        </colgroup>
        <thead><tr><th>标签</th><th>类型</th><th>数量</th><th>存储</th><th>值</th><th>引用 / 值槽</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    ${alerts}`;
}

function renderByteMap() {
  const parsed = state.parsed;
  if (!parsed) return;
  const fileSize = parsed.fileSize || 1;
  els.mapEnd.textContent = offsetLabel(parsed.fileSize);
  els.mapMid.textContent = offsetLabel(Math.floor(parsed.fileSize / 2));
  els.fileMap.innerHTML = (parsed.segments || []).map((segment) => {
    const length = Math.max(1, segment.end - segment.start);
    const isScan = segment.name === "SOS";
    const isExif = segment.name === "APP1" && segment.exif;
    const showLabel = length > 160 || isExif || isScan;
    const label = isExif ? "EXIF" : isScan ? "SCAN" : segment.name;
    return `<div class="map-seg${isScan ? " scan" : isExif ? " exif" : segment.marker >= 0xe0 && segment.marker <= 0xef ? " meta" : ""}"
      style="flex:${length} 1 0" title="${segment.name} · ${offsetLabel(segment.start)}–${offsetLabel(segment.end)} · ${length} B">${showLabel ? label : ""}</div>`;
  }).join("");

  const exifSegment = parsed.exif
    ? parsed.segments.find((segment) => segment.exif)
    : null;
  const scan = parsed.scan;
  els.fileMapCaption.textContent = [
    exifSegment ? `EXIF APP1 ${offsetLabel(exifSegment.start)}–${offsetLabel(exifSegment.end)} · ${formatCount(exifSegment.end - exifSegment.start)} B` : "无 EXIF APP1",
    scan ? `扫描流 ${formatCount(scan.length)} B · ${((scan.length / fileSize) * 100).toFixed(1)}%` : "无扫描流",
  ].join("  /  ");

  const targets = [
    ...(state.plan?.targets.date || []),
    ...(state.plan?.targets.orientation || []),
  ];
  els.targetRanges.innerHTML = targets.length
    ? targets.map((target) => `
      <div class="target-range">
        <i class="range-marker"></i>
        <span><strong>${escapeHtml(target.tagLabel)}</strong> · ${escapeHtml(target.ifdPath)}</span>
        <code>${target.rangeLabel}</code>
        <code>${target.valid ? `${target.length} B / ${target.storage}` : "INVALID"}</code>
      </div>`).join("")
    : '<div class="target-range"><span>没有可写目标字段</span></div>';

  renderTiffMap(targets);
}

function renderTiffMap(targets) {
  const exif = state.parsed?.exif;
  if (!exif || !exif.tiffLength) {
    els.tiffMap.innerHTML = "";
    els.tiffMapSize.textContent = "—";
    return;
  }
  const total = exif.tiffLength;
  const bands = [];
  const band = (start, length, type, title) => {
    const relative = start - exif.tiffStart;
    if (relative < 0 || relative >= total || length <= 0) return;
    const visibleLength = Math.min(length, total - relative);
    bands.push(`<i class="tiff-band ${type}" style="left:${(relative / total) * 100}%;width:${Math.max((visibleLength / total) * 100, 0.45)}%" title="${escapeHtml(title)}"></i>`);
  };
  for (const ifd of exif.ifds) {
    if (ifd.tableStart !== null && ifd.tableEnd !== null) {
      band(ifd.tableStart, ifd.tableEnd - ifd.tableStart, "struct", `${ifd.path} IFD 表`);
    }
  }
  for (const entry of exif.entries) {
    if (entry.storage === "offset" && entry.valid) {
      band(entry.dataFileOffset, entry.dataSize, "external", `${entry.ifdPath}.${entry.tagLabel} 外置字段`);
    }
  }
  for (const target of targets) {
    if (!target.valid) continue;
    band(target.fileOffset, target.length, "target", `${target.ifdPath}.${target.tagLabel} 可写值槽`);
  }
  els.tiffMap.innerHTML = bands.join("");
  els.tiffMapSize.textContent = `${formatCount(total)} B · TIFF @ ${offsetLabel(exif.tiffStart)}`;
}

function renderPreflight() {
  const plan = state.plan;
  if (!state.parsed) {
    els.preflightStatus.textContent = "WAITING";
    els.preflightStatus.className = "status-badge";
    els.preflightBody.innerHTML = '<p class="muted-copy">载入素材后显示目标字段与可写区间。</p>';
    els.writeButton.disabled = true;
    els.writeButton.classList.remove("is-blocked");
    els.writeButtonLabel.textContent = "等待素材";
    els.quickDownload.hidden = true;
    return;
  }
  if (!plan) return;

  const hardBlock = plan.errors.some((issue) => STRUCTURAL_ERRORS.has(issue.code));
  let statusClass = "ok";
  let statusText = "READY";
  if (plan.errors.length) {
    statusClass = "blocked";
    statusText = hardBlock ? "BLOCKED" : "CHECK";
    els.preflightStatus.textContent = statusText;
    els.preflightStatus.className = "status-badge blocked";
  } else if (plan.warnings.length) {
    statusClass = "warn";
    statusText = "REVIEW";
    els.preflightStatus.textContent = statusText;
    els.preflightStatus.className = "status-badge warn";
  } else {
    els.preflightStatus.textContent = statusText;
    els.preflightStatus.className = "status-badge ok";
  }

  const dateTargets = plan.targets.date;
  const orientationTargets = plan.targets.orientation;
  const duplicateDate = dateTargets.filter((target) => target.duplicateInIfd).length;
  const duplicateOrientation = orientationTargets.filter((target) => target.duplicateInIfd).length;
  const summaryRows = [
    {
      tone: dateTargets.length ? "" : "error",
      title: `DateTimeOriginal × ${dateTargets.length}`,
      detail: dateTargets.length
        ? dateTargets.map((target) => `${target.ifdPath} · ${target.storage === "inline" ? "内联" : "外置"} · ${target.length} B`).join("<br>")
        : "未找到可写目标",
    },
    {
      tone: orientationTargets.length ? "" : "error",
      title: `Orientation × ${orientationTargets.length}`,
      detail: orientationTargets.length
        ? orientationTargets.map((target) => `${target.ifdPath} · ${target.storage} · ${target.length} B`).join("<br>")
        : "未找到可写目标",
    },
  ];
  if (duplicateDate || duplicateOrientation) {
    summaryRows.push({
      tone: "warn",
      title: "重复标签：同步写入全部副本",
      detail: `日期重复 ${duplicateDate} 处 / 方向重复 ${duplicateOrientation} 处；不会留下单边旧值。`,
    });
  }
  if (!plan.errors.length) {
    summaryRows.push({
      tone: "",
      title: "写入边界已锁定",
      detail: `${plan.plannedRanges.length} 个值槽，全部位于现有 TIFF 字段内部。`,
    });
  }

  const diagnostics = [
    ...plan.errors.map((issue) => ({ tone: "error", message: issue.message })),
    ...plan.warnings.map((issue) => ({ tone: "warn", message: issue.message })),
  ].slice(0, 7);
  const ranges = plan.plannedRanges.slice(0, 8).map((range) => `
    <div class="range-list-row">
      <span>${escapeHtml(range.label)}</span>
      <span>${offsetLabel(range.offset)} +${range.length}</span>
    </div>`).join("");

  els.preflightBody.innerHTML = `
    <div class="plan-summary">
      ${summaryRows.map((row) => `
        <div class="plan-row">
          <i class="plan-dot ${row.tone}"></i>
          <span><strong>${escapeHtml(row.title)}</strong><small>${row.detail}</small></span>
        </div>`).join("")}
    </div>
    ${diagnostics.length ? `<div class="diagnostics">${diagnostics.map((item) =>
      `<div class="diagnostic ${item.tone}">${escapeHtml(item.message)}</div>`).join("")}</div>` : ""}
    ${ranges ? `<div class="range-list"><span class="range-list-label">计划写入区间</span>${ranges}</div>` : ""}`;

  if (plan.ok) {
    els.writeButton.disabled = false;
    els.writeButton.classList.remove("is-blocked");
    els.writeButtonLabel.textContent = state.result?.ok ? "重新执行定点写入" : "执行定点写入";
  } else if (hardBlock) {
    els.writeButton.disabled = true;
    els.writeButton.classList.add("is-blocked");
    els.writeButtonLabel.textContent = "已阻止写出 · 越界引用";
  } else {
    els.writeButton.disabled = true;
    els.writeButton.classList.remove("is-blocked");
    els.writeButtonLabel.textContent = "修正输入后写入";
  }
}

function runPatch() {
  if (!state.parsed || !state.plan?.ok) {
    showToast("写前检查未通过，不能生成补丁。", "error");
    renderPreflight();
    return;
  }
  const result = applyEdit(state.parsed, { plan: state.plan });
  state.result = result;
  if (result.ok) {
    showToast(`定点写入完成：${result.changedBytes.length} B 变化，扫描哈希一致。`);
  } else {
    showToast("补丁未通过写后回读，已拒绝输出。", "error");
  }
  renderResult();
  renderPreviewState();
  renderPreflight();
  if (result.ok) {
    setTimeout(() => els.resultCard.scrollIntoView({ behavior: "smooth", block: "start" }), 40);
  }
}

function renderResult() {
  const result = state.result;
  if (!result) {
    els.resultCard.hidden = true;
    els.quickDownload.hidden = true;
    return;
  }
  els.resultCard.hidden = false;
  els.verifyPill.textContent = result.ok ? "VERIFICATION PASS" : "VERIFICATION FAIL";
  els.verifyPill.className = `verify-pill${result.ok ? "" : " fail"}`;
  const checks = result.verification?.checks || [];
  els.verificationStrip.innerHTML = checks.map((check) => `
    <div class="check-item${check.passed ? "" : " fail"}">
      <span class="check-icon">${check.passed ? "✓" : "×"}</span>
      <span><strong>${escapeHtml(check.label)}</strong><small title="${escapeHtml(check.detail)}">${escapeHtml(check.detail)}</small></span>
    </div>`).join("");
  const scan = result.verification?.scan;
  els.scanCompare.innerHTML = `
    <div class="hash-block"><span>写入前 · SOS → EOI</span><code>${escapeHtml(scan?.beforeHash || "—")}</code></div>
    <div class="hash-block after"><span>写入后 · SOS → EOI ${scan?.equal ? "✓ IDENTICAL" : "× CHANGED"}</span><code>${escapeHtml(scan?.afterHash || "—")}</code></div>`;
  els.changeBody.innerHTML = result.changedRanges.length
    ? result.changedRanges.map((range) => `
      <tr>
        <td>${offsetLabel(range.start)}</td>
        <td>${range.length} B</td>
        <td>${escapeHtml(range.labels.join(" + ") || "—")}</td>
        <td>${escapeHtml(range.beforeHex)}</td>
        <td>${escapeHtml(range.afterHex)}</td>
      </tr>`).join("")
    : '<tr><td>—</td><td>0 B</td><td>无字段值变化</td><td>—</td><td>—</td></tr>';
  els.downloadButton.hidden = !result.ok;
  els.quickDownload.hidden = !result.ok;
  els.resultFootnote.textContent = result.ok
    ? `${result.changedBytes.length} B 变化 · ${result.changedRanges.length} 个连续区间 · 输出 ${formatBytes(result.output.length)}`
    : result.errors.map((error) => error.message).join(" / ");
}

function downloadResult() {
  if (!state.result?.ok || !state.result.output) {
    showToast("没有通过验证的输出副本。", "error");
    return;
  }
  const blob = new Blob([state.result.output], { type: "image/jpeg" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  const baseName = (state.fileName || "asset.jpg").replace(/\.jpe?g$/i, "");
  anchor.href = url;
  anchor.download = `${baseName}.pinpoint.jpg`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  showToast("已导出通过扫描哈希校验的归档副本。");
}

initialize();
