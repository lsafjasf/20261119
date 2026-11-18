(function () {
  const sourceData = window.CUTTRACE_DATA;
  const Audit = window.CutTraceAudit;
  const state = {
    data: JSON.parse(JSON.stringify(sourceData)),
    audit: null,
    filter: "all",
    selectedClipId: "CLIP-06",
    selectedDifferenceKey: null,
    selectedCandidateId: null,
    modal: null,
    returnFocus: null,
    toastTimer: null,
    scanTimer: null,
  };

  const STATUS_STYLE = {
    available: {
      color: "var(--lime)",
      soft: "var(--lime-soft)",
      line: "rgba(200, 245, 110, 0.28)",
      icon: "check",
    },
    conflict: {
      color: "var(--amber)",
      soft: "var(--amber-soft)",
      line: "rgba(241, 184, 93, 0.28)",
      icon: "alert",
    },
    missing: {
      color: "var(--red)",
      soft: "var(--red-soft)",
      line: "rgba(255, 111, 104, 0.3)",
      icon: "offline",
    },
    ambiguous: {
      color: "var(--violet)",
      soft: "var(--violet-soft)",
      line: "rgba(184, 164, 255, 0.3)",
      icon: "split",
    },
  };

  const ICON_PATHS = {
    check: '<circle cx="12" cy="12" r="8"/><path d="m8.5 12 2.3 2.3 4.7-5"/>',
    alert: '<path d="M12 3.8 3.5 19h17L12 3.8Z"/><path d="M12 9v4.5M12 16.7h.01"/>',
    offline:
      '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="m5 19 14-14M8 10h.01M16 14h.01"/>',
    split:
      '<circle cx="8" cy="12" r="4"/><circle cx="16" cy="12" r="4"/><path d="M12 5v14"/>',
    play: '<path d="m9 6 9 6-9 6V6Z"/>',
    rewind: '<path d="M6 5v14M18 6l-8 6 8 6V6Z"/>',
    timeline: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M8 9v6M12 9v6M16 9v6"/>',
    lock: '<path d="M8 11V8a4 4 0 0 1 8 0v3"/><rect x="5" y="11" width="14" height="10" rx="2"/>',
    copy: '<rect x="8" y="8" width="11" height="11" rx="1.5"/><path d="M16 8V5H5v11h3"/>',
    search:
      '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/><path d="M8.7 11h4.6"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    shield:
      '<path d="M12 3 5 6v5c0 4.6 2.9 8.2 7 10 4.1-1.8 7-5.4 7-10V6l-7-3Z"/><path d="m9 12 2 2 4-4"/>',
  };

  const els = {
    stats: document.getElementById("stats"),
    stage: document.getElementById("stage"),
    filters: document.getElementById("filters"),
    mappingTable: document.getElementById("mappingTable"),
    inspector: document.getElementById("inspector"),
    modalRoot: document.getElementById("modalRoot"),
    toast: document.getElementById("toast"),
    exportBtn: document.getElementById("exportBtn"),
    rescanBtn: document.getElementById("rescanBtn"),
    traceGate: document.getElementById("traceGate"),
    traceGateNote: document.getElementById("traceGateNote"),
    navAllCount: document.getElementById("navAllCount"),
    navIssueCount: document.getElementById("navIssueCount"),
  };

  function icon(name) {
    return `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON_PATHS[name] || ""}</svg>`;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function statusPill(status, label) {
    const style = STATUS_STYLE[status];
    return `<span class="status-pill" style="--status-color:${style.color};--status-soft:${style.soft};--status-line:${style.line}">${escapeHtml(label)}</span>`;
  }

  function selectedEntry() {
    return state.audit.clips.find((entry) => entry.clipId === state.selectedClipId) || state.audit.clips[0];
  }

  function filteredClips() {
    if (state.filter === "all") return state.audit.clips;
    if (state.filter === "issues") {
      return state.audit.clips.filter((entry) => entry.status !== Audit.STATUS.AVAILABLE);
    }
    if (state.filter === "resolved") {
      return state.audit.clips.filter((entry) => Boolean(entry.proxy));
    }
    return state.audit.clips.filter((entry) => entry.status === state.filter);
  }

  function showToast(message, tone) {
    window.clearTimeout(state.toastTimer);
    els.toast.innerHTML = message;
    els.toast.className = `toast is-visible${tone === "danger" ? " is-danger" : ""}`;
    state.toastTimer = window.setTimeout(() => {
      els.toast.className = "toast";
    }, 3600);
  }

  function renderHeader() {
    const gate = state.audit.gate;
    const note = document.querySelector(".header-note");
    els.traceGate.textContent = gate.blocked ? "BLOCKED" : "CLEAR";
    els.traceGateNote.textContent = gate.reason;
    note.classList.toggle("is-clear", !gate.blocked && state.audit.summary.conflict === 0);
    els.navAllCount.textContent = state.audit.summary.total;
    els.navIssueCount.textContent = state.audit.summary.differences;
  }

  function renderStats() {
    const definitions = [
      { key: "available", label: "Available", note: "source verified", icon: "check" },
      { key: "conflict", label: "Conflicts", note: "metadata drift", icon: "alert" },
      { key: "missing", label: "Missing proxy", note: "output blocked", icon: "offline" },
      { key: "ambiguous", label: "Ambiguous", note: "needs decision", icon: "split" },
    ];

    els.stats.innerHTML = definitions
      .map((item, index) => {
        const style = STATUS_STYLE[item.key];
        const active = state.filter === item.key;
        return `
          <button class="stat-card${active ? " is-active" : ""}" type="button"
            data-filter="${item.key}" aria-pressed="${active}"
            style="--stat-color:${style.color};--stat-soft:${style.soft}">
            <span class="stat-icon">${icon(item.icon)}</span>
            <span class="stat-copy">
              <span>${item.label} · ${item.note}</span>
              <strong>${state.audit.summary[item.key]} <small>/ ${state.audit.summary.total}</small></strong>
            </span>
            <span class="stat-index">0${index + 1}</span>
          </button>
        `;
      })
      .join("");
  }

  function renderTimeline() {
    const entries = state.audit.clips;
    const totalFrames = entries.reduce(
      (total, entry) => total + (entry.original?.durationFrames || 1),
      0
    );
    const ruler = Array.from({ length: 6 }, (_, index) => {
      const frame = Math.round((state.audit.sequence.durationFrames / 5) * index);
      return `<span>${Audit.formatTimecode(frame, state.audit.sequence.frameRate)}</span>`;
    }).join("");

    const clips = entries
      .map((entry) => {
        const style = STATUS_STYLE[entry.status];
        const selected = entry.clipId === state.selectedClipId;
        const duration = entry.original?.durationFrames || 1;
        const label =
          entry.status === Audit.STATUS.MISSING
            ? "NO PROXY"
            : entry.status === Audit.STATUS.AMBIGUOUS
              ? "NEEDS MATCH"
              : entry.statusMeta.shortLabel.toUpperCase();
        return `
          <button class="timeline-clip ${entry.art} status-${entry.status}${selected ? " is-selected" : ""}"
            type="button" data-clip-id="${entry.clipId}" data-filter-none="true"
            aria-label="${escapeHtml(entry.timelineReference)} — ${entry.statusMeta.label}"
            aria-pressed="${selected}"
            style="flex:${duration} 1 0;--clip-color:${style.color}">
            <span class="clip-top"><i></i><span>${escapeHtml(entry.original?.clip || entry.timelineReference)}</span></span>
            <span class="clip-bottom"><span>${duration}f</span><b>${label}</b></span>
          </button>
        `;
      })
      .join("");

    const selected = selectedEntry();
    const gateBlocked = selected.playbackBlocked;
    const gateText = gateBlocked
      ? "TRACE GATE · BLOCKED"
      : selected.status === Audit.STATUS.CONFLICT
        ? "TRACE GATE · REVIEW"
        : "TRACE GATE · VERIFIED";

    els.stage.innerHTML = `
      <section class="timeline-panel" aria-label="Sequence timeline">
        <header class="timeline-head">
          <div class="timeline-title">
            <span>${icon("timeline")}</span>
            <div><small>SEQUENCE</small><strong>SM_040_COMP_v12</strong></div>
          </div>
          <div class="timeline-meta">
            <span><b>${entries.length}</b> CLIPS</span><i></i>
            <span><b>${state.audit.sequence.frameRate}</b> FPS</span><i></i>
            <span><b>${Audit.formatTimecode(totalFrames, state.audit.sequence.frameRate)}</b></span>
          </div>
        </header>
        <div class="timeline-body">
          <div class="ruler">${ruler}</div>
          <span class="track-label">V1</span>
          <div class="clip-lane">${clips}</div>
          <div class="playhead" id="playhead"></div>
        </div>
        <div class="transport">
          <button class="transport-btn" type="button" data-action="rewind" title="Go to sequence start">${icon("rewind")}</button>
          <button class="transport-btn is-main" type="button" data-action="play" title="Attempt playback">${icon("play")}</button>
          <span class="transport-time">${selected.timelineTimecode} <small>/ ${Audit.formatTimecode(totalFrames, state.audit.sequence.frameRate)}</small></span>
          <span class="trace-chip${gateBlocked ? " is-blocked" : ""}"><i></i>${gateText}</span>
        </div>
      </section>
      <section class="monitor-panel" aria-label="Program monitor">${renderMonitor(selected)}</section>
    `;
    window.requestAnimationFrame(positionPlayhead);
  }

  function renderMonitor(entry) {
    const style = STATUS_STYLE[entry.status];
    const badgeClass =
      entry.status === Audit.STATUS.MISSING
        ? "is-danger"
        : entry.status === Audit.STATUS.CONFLICT
          ? "is-warn"
          : entry.status === Audit.STATUS.AMBIGUOUS
            ? "is-violet"
            : "";
    const badge =
      entry.status === Audit.STATUS.AVAILABLE
        ? "SOURCE VERIFIED"
        : entry.statusMeta.label.toUpperCase();

    let frame = "";
    if (entry.status === Audit.STATUS.MISSING) {
      frame = `
        <div class="monitor-offline">
          <span class="offline-icon">${icon("offline")}</span>
          <strong>NO PROXY · NOT SILENCE</strong>
          <p>The editor is blocked at this clip. It will not render an empty frame or substitute audio silence.</p>
        </div>
      `;
    } else if (entry.status === Audit.STATUS.AMBIGUOUS) {
      const [first, second] = entry.candidates;
      frame = `
        <div class="monitor-ambiguous">
          <span class="ambiguity-lock">${icon("lock")} MATCH WITHHELD</span>
          <div class="candidate-ghost art-day2"><span>${escapeHtml(first?.id || "A")} · ${escapeHtml(first?.signature || "")}</span></div>
          <div class="candidate-ghost art-day5"><span>${escapeHtml(second?.id || "B")} · ${escapeHtml(second?.signature || "")}</span></div>
        </div>
      `;
    } else {
      frame = `<div class="monitor-art ${entry.art}"></div>`;
    }

    const safety =
      entry.status === Audit.STATUS.MISSING
        ? { cls: "is-danger", text: "Playback blocked. Missing proxy is never treated as silence or empty video." }
        : entry.status === Audit.STATUS.AMBIGUOUS
          ? { cls: "", text: "Two same-name assets have different content. Choose the intended source before preview." }
          : entry.status === Audit.STATUS.CONFLICT
            ? { cls: "", text: `${entry.diffCount} metadata difference${entry.diffCount === 1 ? "" : "s"} found. Review before output.` }
            : { cls: "is-clear", text: "Proxy is traceable to its original by metadata and source fingerprint." };

    return `
      <header class="monitor-head">
        <span>PROGRAM MONITOR</span>
        <b class="${badgeClass}">${badge}</b>
      </header>
      <div class="monitor-frame" id="monitorFrame">
        ${frame}
        <div class="monitor-caption">
          <div>
            <strong>${escapeHtml(entry.timelineReference)}</strong>
            <span>${escapeHtml(entry.original?.id)} · V1 ${entry.timelineTimecode}</span>
          </div>
          <span class="caption-badge">${entry.playbackBlocked ? "BLOCKED" : entry.status === Audit.STATUS.AVAILABLE ? "TRACE OK" : "CHECK"}</span>
        </div>
      </div>
      <footer class="monitor-safety ${safety.cls}" style="--status-color:${style.color}">
        ${icon(entry.status === Audit.STATUS.AVAILABLE ? "shield" : entry.status === Audit.STATUS.MISSING ? "offline" : "alert")}
        <span>${safety.text}</span>
      </footer>
    `;
  }

  function positionPlayhead() {
    const playhead = document.getElementById("playhead");
    const selected = document.querySelector(".timeline-clip.is-selected");
    const body = document.querySelector(".timeline-body");
    if (!playhead || !selected || !body) return;
    const bodyRect = body.getBoundingClientRect();
    const clipRect = selected.getBoundingClientRect();
    const left = Math.max(38, Math.min(bodyRect.width - 18, clipRect.left - bodyRect.left + 8));
    playhead.style.left = `${left}px`;
  }

  function renderFilters() {
    const filters = [
      { key: "all", label: "All", count: state.audit.summary.total },
      { key: "available", label: "Available", count: state.audit.summary.available },
      { key: "conflict", label: "Conflict", count: state.audit.summary.conflict },
      { key: "missing", label: "Missing", count: state.audit.summary.missing },
      { key: "ambiguous", label: "Ambiguous", count: state.audit.summary.ambiguous },
    ];
    els.filters.innerHTML = filters
      .map(
        (item) => `
          <button class="filter-btn${state.filter === item.key ? " is-active" : ""}"
            type="button" data-filter="${item.key}" aria-pressed="${state.filter === item.key}">
            ${item.label}<b>${item.count}</b>
          </button>
        `
      )
      .join("");
  }

  function renderMapping() {
    const entries = filteredClips();
    if (!entries.length) {
      els.mappingTable.innerHTML = `
        <div class="empty-list">
          ${icon("check")}
          <strong>No clips in this state</strong>
          <span>The current status filter has no matching timeline references.</span>
        </div>
      `;
      return;
    }

    const rows = entries
      .map((entry) => {
        const style = STATUS_STYLE[entry.status];
        const selected = entry.clipId === state.selectedClipId;
        const proxyLabel = entry.proxy
          ? entry.proxy.id
          : entry.status === Audit.STATUS.AMBIGUOUS
            ? `${entry.candidates.length} candidates`
            : "No proxy";
        const originalLabel = entry.original?.id || "Unresolved";
        const deltaLabel =
          entry.status === Audit.STATUS.AVAILABLE
            ? "0 diffs"
            : `${entry.diffCount} diff${entry.diffCount === 1 ? "" : "s"}`;
        return `
          <button class="mapping-row${selected ? " is-selected" : ""}" type="button"
            data-clip-id="${entry.clipId}" aria-pressed="${selected}"
            style="--row-color:${style.color};--row-soft:${style.soft}">
            <span class="row-index">${pad(entry.order)}</span>
            <span class="row-clip">
              <strong>${escapeHtml(entry.timelineReference)}</strong>
              <span>${entry.clipId} · V1 ${entry.timelineTimecode}</span>
            </span>
            <span class="row-link">
              <span>${escapeHtml(proxyLabel)}</span><i>→</i><span>${escapeHtml(originalLabel)}</span>
            </span>
            ${statusPill(entry.status, entry.statusMeta.label)}
            <span class="delta-count"><b>${deltaLabel}</b></span>
          </button>
        `;
      })
      .join("");

    els.mappingTable.innerHTML = `
      <div class="mapping-head" aria-hidden="true">
        <span>#</span><span>CLIP / REFERENCE</span><span>PROXY → ORIGINAL</span><span>STATUS</span><span>DELTA</span>
      </div>
      ${rows}
    `;
  }

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function traceMap(entry) {
    const proxyName = entry.proxy
      ? entry.proxy.id
      : entry.status === Audit.STATUS.AMBIGUOUS
        ? `${entry.candidates.length} candidates`
        : "No proxy";
    const proxySub = entry.proxy
      ? entry.proxy.filename
      : entry.status === Audit.STATUS.AMBIGUOUS
        ? "Match withheld"
        : "Not registered";
    const proxyClass = entry.proxy
      ? ""
      : entry.status === Audit.STATUS.AMBIGUOUS
        ? " is-ambiguous"
        : " is-missing";
    return `
      <div class="trace-map">
        <div class="trace-node">
          <small>TIMELINE REF</small>
          <strong>${escapeHtml(entry.timelineReference)}</strong>
          <span>${entry.clipId} · ${entry.timelineTimecode}</span>
        </div>
        <div class="trace-arrow"><i></i></div>
        <div class="trace-node${proxyClass}">
          <small>PROXY</small>
          <strong>${escapeHtml(proxyName)}</strong>
          <span>${escapeHtml(proxySub)}</span>
        </div>
        <div class="trace-arrow"><i></i></div>
        <div class="trace-node">
          <small>ORIGINAL</small>
          <strong>${escapeHtml(entry.original?.id || "—")}</strong>
          <span>${escapeHtml(entry.original?.filename || "—")}</span>
        </div>
      </div>
    `;
  }

  function traceMethod(entry) {
    const statusText = entry.traceable
      ? "TRACEABLE"
      : entry.status === Audit.STATUS.AMBIGUOUS
        ? "WITHHELD"
        : entry.status === Audit.STATUS.MISSING
          ? "BLOCKED"
          : "UNVERIFIED";
    const statusClass = entry.traceable
      ? ""
      : entry.status === Audit.STATUS.AMBIGUOUS
        ? " is-violet"
        : " is-danger";
    return `
      <div class="trace-method">
        <div><small>MATCH METHOD</small><strong>${escapeHtml(entry.mapping.method)}</strong></div>
        <span class="${statusClass}">${statusText}</span>
      </div>
    `;
  }

  function renderCompareTable(entry) {
    const rows = entry.fields
      .map((field) => {
        const diff = field.verdict === "diff";
        const missing = field.verdict === "missing";
        const diffKey =
          diff || missing
            ? entry.status === Audit.STATUS.AMBIGUOUS
              ? "identity"
              : diff
                ? field.key
                : "proxy"
            : "";
        const focused =
          diffKey &&
          (state.selectedDifferenceKey === diffKey ||
            (diffKey === "proxy" && entry.differences[0]?.key === "proxy"));
        const className = [
          "compare-row",
          diff ? "is-diff" : "",
          missing ? "is-missing" : "",
          focused ? "is-focused" : "",
        ]
          .filter(Boolean)
          .join(" ");
        const inner = `
          <span class="compare-field">${escapeHtml(field.label)}</span>
          <span class="compare-value">${escapeHtml(field.originalValue)}</span>
          <span class="compare-value is-proxy">${escapeHtml(field.proxyValue)}</span>
          <span class="compare-flag">${diff ? "≠" : missing ? "!" : "✓"}</span>
        `;
        return diff || missing
          ? `<button class="${className}" type="button" data-diff-key="${diffKey}">${inner}</button>`
          : `<div class="${className}">${inner}</div>`;
      })
      .join("");

    return `
      <section class="inspector-section">
        <div class="section-head">METADATA COMPARE <span>5 gates</span></div>
        <div class="compare-values-head" aria-hidden="true">
          <span>FIELD</span><span>ORIGINAL</span><span>PROXY</span><span></span>
        </div>
        <div class="compare-table">${rows}</div>
      </section>
    `;
  }

  function renderDifferenceList(entry) {
    if (!entry.differences.length) {
      return `
        <section class="inspector-section">
          <div class="section-head">DISCREPANCIES <span>0</span></div>
          <div class="empty-diffs">${icon("check")} All five metadata gates match <span>· source fingerprint verified</span></div>
        </section>
      `;
    }
    return `
      <section class="inspector-section">
        <div class="section-head">DISCREPANCIES <span>${entry.differences.length}</span></div>
        <div class="diff-list">
          ${entry.differences
            .map((difference) => {
              const focused = state.selectedDifferenceKey === difference.key;
              return `
                <button class="diff-item type-${difference.type}${focused ? " is-focused" : ""}"
                  type="button" data-diff-key="${difference.key}">
                  <span class="diff-dot">${difference.type === "conflict" ? "!" : difference.type === "missing" ? "×" : "?"}</span>
                  <span class="diff-item-main">
                    <strong>${escapeHtml(difference.label)}</strong>
                    <span>${escapeHtml(difference.originalValue)} → ${escapeHtml(difference.proxyValue)}</span>
                  </span>
                  <b>${escapeHtml(difference.delta)}</b>
                </button>
              `;
            })
            .join("")}
        </div>
        ${renderDifferenceDetail(entry)}
      </section>
    `;
  }

  function renderDifferenceDetail(entry) {
    const difference =
      entry.differences.find((item) => item.key === state.selectedDifferenceKey) ||
      entry.differences[0];
    if (!difference) return "";
    const originalAsset = entry.original;
    const proxyAsset = entry.proxy;
    const proxyMeta = proxyAsset
      ? `${proxyAsset.id} · ${proxyAsset.path}`
      : entry.status === Audit.STATUS.AMBIGUOUS
        ? entry.candidates.map((candidate) => candidate.id).join(" / ")
        : "No proxy path registered";
    return `
      <div class="diff-detail">
        <div class="diff-detail-head">
          <strong>${escapeHtml(difference.label)}</strong>
          <span>${escapeHtml(difference.delta)}</span>
        </div>
        <div class="detail-sides">
          <div class="detail-side">
            <small>ORIGINAL · ${escapeHtml(originalAsset?.id || "—")}</small>
            <b>${escapeHtml(difference.originalValue)}</b>
            <span title="${escapeHtml(originalAsset?.path || "")}">${escapeHtml(originalAsset?.path || "—")}</span>
          </div>
          <div class="detail-side">
            <small>PROXY · ${escapeHtml(proxyAsset?.id || "UNRESOLVED")}</small>
            <b>${escapeHtml(difference.proxyValue)}</b>
            <span title="${escapeHtml(proxyMeta)}">${escapeHtml(proxyMeta)}</span>
          </div>
        </div>
        <p>${escapeHtml(difference.message)}</p>
      </div>
    `;
  }

  function renderResolvedBanner(entry) {
    if (!entry.resolvedByUser) return "";
    return `
      <div class="resolved-banner">
        ${icon("check")}
        <span>Match confirmed by user. Filename alone did not authorize this trace.</span>
      </div>
    `;
  }

  function renderCandidateList(entry) {
    if (entry.status !== Audit.STATUS.AMBIGUOUS || !entry.candidates.length) return "";
    return `
      <div class="section-head">SAME-NAME CANDIDATES <span>${entry.candidates.length} files</span></div>
      <div class="candidate-mini-list">
        ${entry.candidates
          .map(
            (candidate, index) => `
              <div class="candidate-mini">
                <span class="candidate-thumb ${index === 0 ? "art-day2" : "art-day5"}"></span>
                <div>
                  <strong>${escapeHtml(candidate.id)} · ${escapeHtml(candidate.path.split("/").slice(-2).join("/"))}</strong>
                  <span>${escapeHtml(candidate.reel)} / ${escapeHtml(candidate.clip)} · ${candidate.durationFrames}f · ${escapeHtml(candidate.signature)}</span>
                </div>
                <b>${candidate.signature === entry.original?.signature ? "SIGNATURE MATCH" : "DIFFERENT"}</b>
              </div>
            `
          )
          .join("")}
      </div>
    `;
  }

  function renderActions(entry) {
    if (entry.status === Audit.STATUS.AMBIGUOUS) {
      return `
        <div class="inspector-actions">
          <button class="btn btn-accent" type="button" data-action="resolve">${icon("lock")} Resolve match</button>
          <button class="btn btn-ghost" type="button" data-action="compare">Compare</button>
        </div>
      `;
    }
    if (entry.status === Audit.STATUS.MISSING) {
      return `
        <div class="inspector-actions">
          <button class="btn btn-accent" type="button" data-action="locate">${icon("search")} Locate proxy</button>
          <button class="btn btn-ghost" type="button" data-copy="${escapeHtml(entry.original?.path || "")}" title="Copy original path">${icon("copy")} Path</button>
        </div>
      `;
    }
    if (entry.status === Audit.STATUS.CONFLICT) {
      return `
        <div class="inspector-actions">
          <button class="btn btn-accent" type="button" data-action="flag">${icon("alert")} Flag for relink</button>
          <button class="btn btn-ghost" type="button" data-action="copy-trace">${icon("copy")} Trace</button>
        </div>
      `;
    }
    return `
      <div class="inspector-actions">
        <button class="btn btn-accent" type="button" data-action="copy-trace">${icon("copy")} Copy trace record</button>
        <button class="btn btn-ghost" type="button" data-action="open-source">Open</button>
      </div>
    `;
  }

  function renderInspector() {
    const entry = selectedEntry();
    const style = STATUS_STYLE[entry.status];
    const originalPath = entry.original?.path || "";
    els.inspector.innerHTML = `
      <div class="inspector-head" style="--inspector-soft:${style.soft}">
        <div class="inspector-topline">
          <span>INSPECTOR · CLIP ${pad(entry.order)}</span>
          ${statusPill(entry.status, entry.statusMeta.label)}
        </div>
        <h2>${escapeHtml(entry.timelineReference)}</h2>
        <div class="inspector-subline">
          <span>${escapeHtml(entry.original?.id || "—")}</span><i></i>
          <span>${entry.clipId} · V1 ${entry.timelineTimecode}</span><i></i>
          <button class="copy-btn" type="button" data-copy="${escapeHtml(originalPath)}" title="Copy original path">
            ${icon("copy")} Copy path
          </button>
        </div>
      </div>
      ${renderResolvedBanner(entry)}
      ${traceMap(entry)}
      ${traceMethod(entry)}
      ${renderCompareTable(entry)}
      ${renderCandidateList(entry)}
      ${renderDifferenceList(entry)}
      ${renderActions(entry)}
    `;
  }

  function renderNav() {
    const links = document.querySelectorAll(".nav-item");
    links.forEach((link) => {
      const filter = link.dataset.filter;
      const scroll = link.dataset.scroll;
      let active = false;
      if (state.filter === "issues") active = filter === "issues";
      else if (state.filter === "resolved") active = filter === "resolved";
      else if (state.filter === "all") active = filter === "all" && !scroll;
      else active = false;
      link.classList.toggle("is-active", active);
    });
  }

  function renderAll() {
    state.audit = Audit.buildAudit(state.data);
    renderHeader();
    renderStats();
    renderFilters();
    renderTimeline();
    renderMapping();
    renderInspector();
    renderNav();
  }

  function selectClip(clipId) {
    const entry = state.audit.clips.find((item) => item.clipId === clipId);
    if (!entry) return;
    state.selectedClipId = clipId;
    state.selectedDifferenceKey = entry.differences[0]?.key || null;
    renderTimeline();
    renderMapping();
    renderInspector();
  }

  function applyFilter(filter, shouldScroll) {
    state.filter = filter;
    const entries = filteredClips();
    if (entries.length && !entries.some((entry) => entry.clipId === state.selectedClipId)) {
      state.selectedClipId = entries[0].clipId;
      state.selectedDifferenceKey = entries[0].differences[0]?.key || null;
    }
    renderStats();
    renderFilters();
    renderTimeline();
    renderMapping();
    renderInspector();
    renderNav();
    if (shouldScroll) {
      const target =
        shouldScroll === "stage"
          ? document.getElementById("stage")
          : shouldScroll === "mapping"
            ? document.getElementById("mapping")
            : null;
      target?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function candidateScan(entry, candidate) {
    return Audit.scanClip(
      { ...entry.clip, proxyId: candidate.id, candidateIds: [] },
      state.data
    );
  }

  function renderResolutionModal(entry) {
    const cards = entry.candidates
      .map((candidate, index) => {
        const scan = candidateScan(entry, candidate);
        const matches = scan.fields.filter((field) => field.equal).length;
        const signatureMatch = candidate.signature === entry.original?.signature;
        const selected = state.selectedCandidateId === candidate.id;
        return `
          <button class="candidate-card${selected ? " is-selected" : ""}" type="button"
            data-select-candidate="${candidate.id}" aria-pressed="${selected}">
            <span class="candidate-radio"></span>
            <span class="candidate-preview ${index === 0 ? "art-day2" : "art-day5"}"></span>
            <span class="candidate-content">
              <small>CANDIDATE ${String.fromCharCode(65 + index)}</small>
              <strong>${escapeHtml(candidate.id)} · ${escapeHtml(candidate.filename)}</strong>
              <p>${escapeHtml(candidate.path)}</p>
              <span class="candidate-meta">
                <span>Reel <b>${escapeHtml(candidate.reel)}</b></span>
                <span>Clip <b>${escapeHtml(candidate.clip)}</b></span>
                <span>Start <b>${candidate.startFrame}f</b></span>
                <span>Rate <b>${Audit.formatFrameRate(candidate.frameRate)}</b></span>
                <span>Duration <b>${escapeHtml(Audit.formatDuration(candidate.durationFrames, candidate.frameRate))}</b></span>
                <span>Content <b>${escapeHtml(candidate.signature)}</b></span>
              </span>
              <span class="candidate-matchline">
                <span>${matches}/5 metadata fields</span>
                <b>${signatureMatch ? "fingerprint match" : "different content"}</b>
              </span>
            </span>
          </button>
        `;
      })
      .join("");

    return `
      <div class="modal-backdrop" data-modal-backdrop>
        <section class="modal" role="dialog" aria-modal="true" aria-labelledby="resolveTitle">
          <header class="modal-head">
            <div>
              <small>SOURCE MATCH REQUIRED</small>
              <h2 id="resolveTitle">Two files share this name.</h2>
              <p>Their content fingerprints differ. Cuttrace will not infer the intended clip from the filename. Inspect both sides and confirm the source.</p>
            </div>
            <button class="modal-close" type="button" data-close-modal aria-label="Close">${icon("close")}</button>
          </header>
          <div class="modal-body">
            <div class="ambiguity-notice">
              ${icon("alert")}
              <span>Timeline reference <strong>${escapeHtml(entry.timelineReference)}</strong> has ${entry.candidates.length} candidates. No proxy association exists until you choose one.</span>
            </div>
            <div class="candidate-grid">${cards}</div>
          </div>
          <footer class="modal-foot">
            <p>Selecting a candidate creates an explicit, auditable user match. Matching content is still checked after confirmation.</p>
            <div>
              <button class="btn btn-ghost" type="button" data-close-modal>Cancel</button>
              <button class="btn btn-accent" type="button" data-confirm-match
                ${state.selectedCandidateId ? "" : "disabled"}>Confirm selected source</button>
            </div>
          </footer>
        </section>
      </div>
    `;
  }

  function renderLocateModal(entry) {
    const folder = (entry.original?.path || "").split("/").slice(0, -1).join("/");
    return `
      <div class="modal-backdrop" data-modal-backdrop>
        <section class="modal" role="dialog" aria-modal="true" aria-labelledby="locateTitle" style="width:min(560px,100%)">
          <header class="modal-head">
            <div>
              <small style="color:var(--red)">MISSING PROXY</small>
              <h2 id="locateTitle">Playback remains blocked.</h2>
              <p>Search for a proxy that matches the original's content fingerprint. A name match alone is not sufficient.</p>
            </div>
            <button class="modal-close" type="button" data-close-modal aria-label="Close">${icon("close")}</button>
          </header>
          <div class="modal-body locate-body">
            <div class="blocked-callout">
              ${icon("offline")}
              <span><strong>${escapeHtml(entry.timelineReference)}</strong> has no verified proxy. The sequence will not insert silence or an empty video frame while this clip is offline.</span>
            </div>
            <label class="field-label">
              Search location
              <input type="text" value="${escapeHtml(folder)}" aria-label="Proxy search location" />
            </label>
            <label class="field-label">
              Required source fingerprint
              <input type="text" value="${escapeHtml(entry.original?.signature || "")}" readonly aria-label="Required source fingerprint" />
            </label>
          </div>
          <footer class="modal-foot">
            <p>Search is non-destructive. The trace gate stays closed until a matching proxy is attached.</p>
            <div>
              <button class="btn btn-ghost" type="button" data-close-modal>Keep blocked</button>
              <button class="btn btn-accent" type="button" data-action="search-proxy">${icon("search")} Search &amp; verify</button>
            </div>
          </footer>
        </section>
      </div>
    `;
  }

  function openModal(type) {
    const entry = selectedEntry();
    state.returnFocus = document.activeElement;
    state.modal = type;
    state.selectedCandidateId = null;
    els.modalRoot.innerHTML =
      type === "resolve" ? renderResolutionModal(entry) : renderLocateModal(entry);
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => {
      const first =
        els.modalRoot.querySelector(".candidate-card") ||
        els.modalRoot.querySelector(".modal-close");
      first?.focus();
    });
  }

  function closeModal(restoreFocus) {
    if (!state.modal) return;
    state.modal = null;
    state.selectedCandidateId = null;
    els.modalRoot.innerHTML = "";
    document.body.style.overflow = "";
    if (restoreFocus !== false && state.returnFocus?.focus) {
      state.returnFocus.focus();
    }
  }

  function chooseCandidate(candidateId) {
    state.selectedCandidateId = candidateId;
    const entry = selectedEntry();
    els.modalRoot.innerHTML = renderResolutionModal(entry);
    window.requestAnimationFrame(() => {
      els.modalRoot.querySelector(`[data-select-candidate="${candidateId}"]`)?.focus();
    });
  }

  function confirmCandidate() {
    const entry = selectedEntry();
    const candidate = entry.candidates.find((item) => item.id === state.selectedCandidateId);
    if (!candidate) return;
    const beforeScan = candidateScan(entry, candidate);
    state.data = Audit.resolveClipCandidate(state.data, entry.clipId, candidate.id);
    state.audit = Audit.buildAudit(state.data);
    const resolved = state.audit.clips.find((item) => item.clipId === entry.clipId);
    state.selectedDifferenceKey = resolved.differences[0]?.key || null;
    state.selectedClipId = entry.clipId;
    if (state.filter === "ambiguous") state.filter = "all";
    closeModal(false);
    renderAll();
    if (beforeScan.status === Audit.STATUS.AVAILABLE) {
      showToast(
        `<strong>User-confirmed proxy.</strong> ${candidate.id} now traces to ${entry.original.id} by metadata and fingerprint.`
      );
    } else {
      showToast(
        `<strong>User-confirmed link, with drift.</strong> ${candidate.id} is attached, but ${beforeScan.diffCount} difference${beforeScan.diffCount === 1 ? "" : "s"} still require relink.`,
        "danger"
      );
    }
  }

  async function copyText(text, message) {
    if (!text) {
      showToast("No path is available for this record.", "danger");
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    showToast(message || "Copied to clipboard.");
  }

  function copyTrace(entry) {
    const record = {
      clipId: entry.clipId,
      timelineReference: entry.timelineReference,
      proxyId: entry.proxy?.id || null,
      originalId: entry.original?.id || null,
      status: entry.status,
      traceable: entry.traceable,
      method: entry.mapping.method,
      differences: entry.differences,
    };
    copyText(JSON.stringify(record, null, 2), "Trace record copied.");
  }

  function exportAudit() {
    const report = Audit.buildReport(state.data);
    const blob = new Blob([JSON.stringify(report, null, 2)], {
      type: "application/json",
    });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${state.audit.sequence.id}_${state.audit.sequence.version}_proxy-audit.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
    showToast(
      `<strong>Audit exported.</strong> ${report.summary.missing} missing, ${report.summary.conflict} conflict, ${report.summary.available} available.`
    );
  }

  function rescan() {
    window.clearTimeout(state.scanTimer);
    els.rescanBtn.disabled = true;
    els.rescanBtn.querySelector("span").textContent = "Scanning…";
    state.scanTimer = window.setTimeout(() => {
      state.audit = Audit.buildAudit(state.data);
      renderAll();
      els.rescanBtn.disabled = false;
      els.rescanBtn.querySelector("span").textContent = "Re-scan";
      showToast(
        `<strong>Scan complete.</strong> ${state.audit.summary.differences} finding${state.audit.summary.differences === 1 ? "" : "s"} across ${state.audit.summary.total} timeline references.`
      );
    }, 650);
  }

  function shakeMonitor() {
    const monitor = document.getElementById("monitorFrame");
    if (!monitor) return;
    monitor.classList.remove("is-shaking");
    void monitor.offsetWidth;
    monitor.classList.add("is-shaking");
    window.setTimeout(() => monitor.classList.remove("is-shaking"), 320);
  }

  function attemptPlayback() {
    const missing = state.audit.clips.find(
      (entry) => entry.status === Audit.STATUS.MISSING
    );
    if (missing) {
      selectClip(missing.clipId);
      window.requestAnimationFrame(() => {
        shakeMonitor();
        showToast(
          `<strong>Playback blocked at ${missing.timelineTimecode}.</strong> ${missing.timelineReference} has no proxy. No silence or empty video was substituted.`,
          "danger"
        );
      });
      return;
    }

    const ambiguous = state.audit.clips.find(
      (entry) => entry.status === Audit.STATUS.AMBIGUOUS
    );
    if (ambiguous) {
      selectClip(ambiguous.clipId);
      showToast(
        `<strong>Source match required.</strong> ${ambiguous.timelineReference} has two same-name candidates.`,
        "danger"
      );
      return;
    }

    if (state.audit.summary.conflict > 0) {
      showToast(
        `<strong>Preview only.</strong> ${state.audit.summary.conflict} linked proxies still carry metadata conflicts. Output remains unresolved.`
      );
      return;
    }

    showToast("<strong>Preview ready.</strong> Every timeline proxy is traceable to its source.");
  }

  function handleAction(action) {
    const entry = selectedEntry();
    if (action === "play") {
      attemptPlayback();
    } else if (action === "rewind") {
      selectClip(state.audit.clips[0].clipId);
    } else if (action === "resolve" || action === "compare") {
      if (entry.status === Audit.STATUS.AMBIGUOUS) openModal("resolve");
    } else if (action === "locate") {
      openModal("locate");
    } else if (action === "search-proxy") {
      closeModal(false);
      showToast(
        `<strong>No verified proxy found.</strong> ${entry.timelineReference} remains blocked at ${entry.timelineTimecode}; no silent substitute was created.`,
        "danger"
      );
    } else if (action === "flag") {
      showToast(
        `<strong>Flagged for relink.</strong> ${entry.timelineReference} carries ${entry.diffCount} unresolved metadata difference${entry.diffCount === 1 ? "" : "s"}.`
      );
    } else if (action === "copy-trace") {
      copyTrace(entry);
    } else if (action === "open-source") {
      copyText(entry.original?.path || "", "Original source path copied.");
    }
  }

  els.exportBtn.addEventListener("click", exportAudit);
  els.rescanBtn.addEventListener("click", rescan);

  document.addEventListener("click", (event) => {
    const actionEl = event.target.closest("[data-action]");
    if (actionEl) {
      handleAction(actionEl.dataset.action);
      return;
    }

    const closeEl = event.target.closest("[data-close-modal]");
    if (closeEl) {
      closeModal();
      return;
    }

    if (event.target.matches("[data-modal-backdrop]")) {
      closeModal();
      return;
    }

    const confirmEl = event.target.closest("[data-confirm-match]");
    if (confirmEl) {
      if (!confirmEl.disabled) confirmCandidate();
      return;
    }

    const candidateEl = event.target.closest("[data-select-candidate]");
    if (candidateEl) {
      chooseCandidate(candidateEl.dataset.selectCandidate);
      return;
    }

    const copyEl = event.target.closest("[data-copy]");
    if (copyEl) {
      copyText(copyEl.dataset.copy, "Path copied.");
      return;
    }

    const diffEl = event.target.closest("[data-diff-key]");
    if (diffEl) {
      state.selectedDifferenceKey = diffEl.dataset.diffKey;
      renderInspector();
      return;
    }

    const clipEl = event.target.closest("[data-clip-id]");
    if (clipEl) {
      selectClip(clipEl.dataset.clipId);
      return;
    }

    const filterEl = event.target.closest("[data-filter]");
    if (filterEl) {
      const scroll = filterEl.dataset.scroll || false;
      applyFilter(filterEl.dataset.filter, scroll);
      if (filterEl.classList.contains("nav-item") && filterEl.dataset.filter === "all" && !scroll) {
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.modal) {
      closeModal();
      return;
    }
    if (event.key !== "Tab" || !state.modal) return;
    const focusable = Array.from(
      els.modalRoot.querySelectorAll(
        'button:not([disabled]), input, [tabindex]:not([tabindex="-1"])'
      )
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  window.addEventListener("resize", () => {
    window.requestAnimationFrame(positionPlayhead);
  });

  function init() {
    state.audit = Audit.buildAudit(state.data);
    const initial = selectedEntry();
    state.selectedDifferenceKey = initial.differences[0]?.key || null;
    renderAll();
  }

  init();

  window.CutTraceApp = {
    state,
    selectClip,
    getAudit: () => Audit.buildAudit(state.data),
  };
})();
