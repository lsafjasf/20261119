import { REPORT_STYLES } from "./report-styles.js";

const EVENT_LABELS = {
  create: "创建",
  clone: "克隆",
  "transfer-out": "发出转移",
  "transfer-in": "接收转移",
  use: "使用",
  close: "关闭",
  warning: "告警",
  stage: "阶段",
};

const DIAGNOSTIC_LABELS = {
  "double-close": "重复关闭",
  "use-after-close": "关闭后使用",
  "unreleased-frame": "帧未释放",
  "invalid-transfer": "转移状态错误",
  "use-while-in-transit": "在途使用",
  "frame-unreachable": "弱引用已释放",
};

const EVENT_MARKS = {
  create: { color: "#22c55e", label: "C" },
  clone: { color: "#3b82f6", label: "K" },
  "transfer-out": { color: "#a855f7", label: "T" },
  "transfer-in": { color: "#8b5cf6", label: "A" },
  use: { color: "#14b8a6", label: "U" },
  close: { color: "#ef4444", label: "X" },
  warning: { color: "#f59e0b", label: "!" },
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatTime(value) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return escapeHtml(value);
  }
  return date.toISOString().slice(11, 23);
}

function frameState(frame) {
  if (frame.state === "closed") {
    return { label: "已释放", className: "closed" };
  }
  if (frame.state === "in_transit") {
    return { label: "在途", className: "transit" };
  }
  return { label: "存活", className: "live" };
}

function stageNoteMap(scenario) {
  return new Map(scenario.stages.map((stage) => [stage.label, stage.note]));
}

function frameEventRows(frame) {
  if (frame.events.length === 0) {
    return '<tr><td colspan="4" class="empty">暂无事件</td></tr>';
  }
  return frame.events
    .map((event) => {
      const label = EVENT_LABELS[event.type] ?? event.type;
      let detail = "";
      if (event.type === "transfer-out") {
        detail = `${escapeHtml(event.from)} → ${escapeHtml(event.to)}`;
      } else if (event.type === "transfer-in") {
        detail = `${escapeHtml(event.owner)} 已接管`;
      } else if (event.type === "clone") {
        detail = `父帧 ${escapeHtml(event.parentId)}`;
      } else if (event.type === "close") {
        detail = escapeHtml(event.reason);
      } else if (event.type === "use") {
        detail = escapeHtml(event.operation);
      } else if (event.type === "warning") {
        detail = DIAGNOSTIC_LABELS[event.code] ?? event.code;
      }
      return `<tr>
        <td>${event.eventIndex}</td>
        <td><span class="event-pill event-${escapeHtml(event.type)}">${escapeHtml(label)}</span></td>
        <td class="stage-cell">${escapeHtml(event.stage ?? "—")}</td>
        <td>${detail || "—"}</td>
      </tr>`;
    })
    .join("");
}

function renderTimeline(scenario) {
  const stages = scenario.stages;
  const frames = scenario.frames;
  if (stages.length === 0) {
    return '<p class="empty">没有阶段快照</p>';
  }
  const labelWidth = 128;
  const stageWidth = 92;
  const laneHeight = 48;
  const topHeight = 44;
  const width = labelWidth + stages.length * stageWidth + 26;
  const height = topHeight + Math.max(frames.length, 1) * laneHeight + 20;
  const stageX = new Map(stages.map((stage, index) => [stage.label, labelWidth + index * stageWidth + 45]));
  const frameY = new Map(frames.map((frame, index) => [frame.id, topHeight + index * laneHeight + 23]));
  const elements = [];

  stages.forEach((stage, index) => {
    const x = labelWidth + index * stageWidth;
    elements.push(
      `<line class="grid-line" x1="${x}" y1="${topHeight - 8}" x2="${x}" y2="${height - 10}" />`,
      `<text class="stage-label" x="${x + 8}" y="19">${escapeHtml(stage.label)}</text>`,
      `<text class="stage-count" x="${x + 8}" y="34">${stage.openCount} 存活 / ${stage.inTransitCount} 在途</text>`,
    );
  });

  for (const frame of frames) {
    const y = frameY.get(frame.id);
    const firstEvent = frame.events.find((event) => event.type === "create" || event.type === "clone");
    const closeEvent = [...frame.events].reverse().find((event) => event.type === "close");
    const lastEvent = frame.events.at(-1);
    const firstX = stageX.get(firstEvent?.stage) ?? labelWidth + 10;
    const lastX = stageX.get(closeEvent?.stage ?? lastEvent?.stage) ?? firstX;
    const state = frameState(frame);
    elements.push(
      `<text class="frame-label" x="10" y="${y + 4}">${escapeHtml(frame.id)}</text>`,
      `<text class="frame-caption" x="10" y="${y + 17}">${escapeHtml(frame.label)}</text>`,
      `<line class="lifeline ${state.className}" x1="${labelWidth}" y1="${y}" x2="${width - 20}" y2="${y}" />`,
      `<line class="life-span ${state.className}" x1="${firstX}" y1="${y}" x2="${lastX}" y2="${y}" />`,
    );

    const perStageCounts = new Map();
    for (const event of frame.events) {
      if (event.type === "stage" || !stageX.has(event.stage)) {
        continue;
      }
      const count = perStageCounts.get(event.stage) ?? 0;
      perStageCounts.set(event.stage, count + 1);
      const x = stageX.get(event.stage) + count * 9;
      const mark = EVENT_MARKS[event.type] ?? { color: "#64748b", label: "?" };
      const label = EVENT_LABELS[event.type] ?? event.type;
      elements.push(
        `<g class="event-mark"><title>${escapeHtml(frame.id)} · ${escapeHtml(label)} · ${escapeHtml(event.stage)}</title>
          <circle cx="${x}" cy="${y}" r="7" fill="${mark.color}" />
          <text x="${x}" y="${y + 3}">${escapeHtml(mark.label)}</text>
        </g>`,
      );
    }
  }

  return `<div class="timeline-wrap">
    <svg class="timeline" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(scenario.title)} 生命周期图">
      <line class="axis" x1="${labelWidth}" y1="${topHeight - 8}" x2="${width - 20}" y2="${topHeight - 8}" />
      ${elements.join("")}
    </svg>
  </div>
  <div class="legend">
    <span><i style="background:#22c55e"></i>创建</span>
    <span><i style="background:#3b82f6"></i>克隆</span>
    <span><i style="background:#a855f7"></i>转移</span>
    <span><i style="background:#14b8a6"></i>使用</span>
    <span><i style="background:#ef4444"></i>关闭</span>
    <span><i style="background:#f59e0b"></i>告警</span>
  </div>`;
}

function renderFrame(frame, notes) {
  const state = frameState(frame);
  return `<details class="frame">
    <summary>
      <span class="frame-id">${escapeHtml(frame.id)}</span>
      <span>${escapeHtml(frame.label)}</span>
      <span class="owner">${escapeHtml(frame.owner)}</span>
      <span class="state ${state.className}">${state.label}</span>
      <span class="event-total">${frame.events.length} 个事件</span>
    </summary>
    <div class="frame-body">
      <div class="frame-meta">
        <span>父帧：<b>${escapeHtml(frame.parentId ?? "—")}</b></span>
        <span>创建：<b>${formatTime(frame.createdAt)}</b></span>
        <span>关闭：<b>${formatTime(frame.closedAt)}</b></span>
        <span>最后阶段：<b>${escapeHtml(frame.lastStage ?? "—")}</b></span>
      </div>
      <table class="event-table">
        <thead><tr><th>#</th><th>事件</th><th>阶段</th><th>详情</th></tr></thead>
        <tbody>${frameEventRows(frame)}</tbody>
      </table>
      <p class="stage-context">${escapeHtml(notes.get(frame.lastStage) || "该帧没有额外阶段说明。")}</p>
    </div>
  </details>`;
}

function renderStageTable(scenario) {
  const rows = scenario.stages
    .map((stage) => {
      const owners = Object.entries(stage.byOwner)
        .map(([owner, count]) => `${escapeHtml(owner)}:${count}`)
        .join(", ");
      const ids = stage.openFrameIds.length
        ? stage.openFrameIds.map((id) => `<code>${escapeHtml(id)}</code>`).join(" ")
        : '<span class="muted">无</span>';
      return `<tr>
        <td><b>${escapeHtml(stage.label)}</b><small>${escapeHtml(stage.note)}</small></td>
        <td class="number">${stage.liveCount}</td>
        <td class="number transit-number">${stage.inTransitCount}</td>
        <td>${owners || '<span class="muted">—</span>'}</td>
        <td>${ids}</td>
      </tr>`;
    })
    .join("");
  return `<div class="table-wrap">
    <table class="stage-table">
      <thead><tr><th>阶段</th><th>存活</th><th>在途</th><th>按所有者</th><th>存活帧 ID</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>`;
}

function renderDiagnostics(scenario) {
  if (scenario.diagnostics.length === 0) {
    return '<p class="all-clear">未检测到所有权告警。</p>';
  }
  return `<div class="diagnostics">${scenario.diagnostics
    .map(
      (diagnostic) => `<article class="diagnostic ${escapeHtml(diagnostic.severity)}">
        <div class="diagnostic-head">
          <span class="diagnostic-code">${escapeHtml(diagnostic.code)}</span>
          <span class="diagnostic-label">${escapeHtml(
            DIAGNOSTIC_LABELS[diagnostic.code] ?? diagnostic.code,
          )}</span>
          <span class="diagnostic-severity">${escapeHtml(diagnostic.severity)}</span>
        </div>
        <p>${escapeHtml(diagnostic.message)}</p>
        <div class="diagnostic-location">
          <span>frame <code>${escapeHtml(diagnostic.frameId ?? "—")}</code></span>
          <span>stage <code>${escapeHtml(diagnostic.stage)}</code></span>
          <span>event <code>#${diagnostic.eventIndex}</code></span>
        </div>
      </article>`,
    )
    .join("")}</div>`;
}

function renderCleanup(scenario) {
  const cleanup = scenario.cleanup;
  const liveness = scenario.liveness;
  return `<div class="cleanup ${cleanup.clean ? "pass" : "fail"}">
    <div class="cleanup-status">
      <span class="status-dot"></span>
      <b>${cleanup.clean ? "清理完成" : "仍有残留"}</b>
      <span>${cleanup.residualCount} 帧未释放</span>
    </div>
    <dl>
      <div><dt>末阶段存活</dt><dd>${cleanup.finalLiveCount}</dd></div>
      <div><dt>已追踪帧</dt><dd>${cleanup.closedFrameCount}/${cleanup.trackedFrameCount}</dd></div>
      <div><dt>close 事件</dt><dd>${cleanup.closeEventCount}</dd></div>
      <div><dt>追踪器强引用</dt><dd>${liveness.strongFrameReferencesInTracker}</dd></div>
      <div><dt>弱引用记录</dt><dd>${liveness.recordReference}</dd></div>
      <div><dt>可解引用目标</dt><dd>${liveness.reachableWeakTargets} <small>可能由管线局部持有</small></dd></div>
    </dl>
    <p>残留 ID：${
      cleanup.residualFrameIds.length
        ? cleanup.residualFrameIds.map((id) => `<code>${escapeHtml(id)}</code>`).join(" ")
        : '<code>无</code>'
    }。追踪器仅以 <code>WeakRef</code> 观察帧，不参与引用计数。</p>
  </div>`;
}

function renderScenario(scenario) {
  const notes = stageNoteMap(scenario);
  const outcome = scenario.outcome.replaceAll("-", " ");
  return `<details class="scenario" open>
    <summary>
      <div>
        <span class="eyebrow">${escapeHtml(scenario.id)}</span>
        <h2>${escapeHtml(scenario.title)}</h2>
        <p>${escapeHtml(scenario.description)}</p>
      </div>
      <div class="scenario-stats">
        <span class="outcome">${escapeHtml(outcome)}</span>
        <span>${scenario.summary.frameCount} 帧</span>
        <span>${scenario.summary.diagnosticCount} 告警</span>
      </div>
    </summary>
    <div class="scenario-body">
      <section>
        <h3>阶段快照</h3>
        ${renderStageTable(scenario)}
      </section>
      <section>
        <h3>生命周期图</h3>
        ${renderTimeline(scenario)}
      </section>
      <section>
        <h3>所有权诊断</h3>
        ${renderDiagnostics(scenario)}
      </section>
      <section>
        <h3>清理证据</h3>
        ${renderCleanup(scenario)}
      </section>
      <section>
        <h3>逐帧事件（点击展开）</h3>
        <div class="frames">${scenario.frames
          .map((frame) => renderFrame(frame, notes))
          .join("")}</div>
      </section>
    </div>
  </details>`;
}

export function renderReport(data) {
  const summary = data.summary;
  const cleanText = summary.allClean
    ? "所有示例场景均已闭环：追踪帧全部释放，追踪器没有保留任何强引用。"
    : "仍有帧未释放，请查看下方场景中的诊断与残留 ID。";
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>VideoFrame 所有权生命周期 | ${escapeHtml(data.generatedAt)}</title>
  <style>${REPORT_STYLES}</style>
</head>
<body>
  <main>
    <header class="hero">
      <div>
        <span class="eyebrow">Ownership tracer / WebCodecs</span>
        <h1>VideoFrame 生命周期</h1>
        <p class="subtitle">以帧 ID 串联创建、克隆、跨 Worker 转移、使用与关闭事件。告警定位到具体阶段，弱引用追踪避免工具自身制造泄漏。</p>
      </div>
      <div class="run-meta">
        <div>生成于 ${escapeHtml(data.generatedAt)}</div>
        <div><code>npm run demo</code></div>
      </div>
    </header>

    <section class="kpis" aria-label="总览">
      <div class="kpi"><b>${summary.pipelinePaths}</b><span>管线路径</span></div>
      <div class="kpi"><b>${summary.framesCreated}</b><span>创建 / 克隆帧</span></div>
      <div class="kpi ok"><b>${summary.framesClosed}/${summary.framesCreated}</b><span>已释放</span></div>
      <div class="kpi ${summary.residualFrames === 0 ? "ok" : ""}"><b>${summary.residualFrames}</b><span>最终残留帧</span></div>
    </section>
    <div class="global-message">${cleanText} 共记录 ${summary.diagnostics} 条诊断；主动故障注入覆盖重复关闭、关闭后使用和未释放帧。</div>

    ${data.scenarios.map(renderScenario).join("")}

    <footer>
      追踪存储：<code>WeakMap&lt;VideoFrame, metadata&gt;</code> + <code>WeakRef&lt;VideoFrame&gt;</code>。
      事件索引从 0 开始；告警中的 <code>event #</code> 可对应逐帧事件表。
    </footer>
  </main>
</body>
</html>`;
}

export function summarizeForConsole(data) {
  const lines = [];
  for (const scenario of data.scenarios) {
    lines.push(`\n[${scenario.id}] ${scenario.title}`);
    for (const stage of scenario.stages) {
      lines.push(
        `  ${stage.label.padEnd(28)} live=${String(stage.liveCount).padEnd(2)} transit=${String(
          stage.inTransitCount,
        ).padEnd(2)} ids=${stage.openFrameIds.join(",") || "-"}`,
      );
    }
    for (const diagnostic of scenario.diagnostics) {
      lines.push(
        `  ! ${diagnostic.code} frame=${diagnostic.frameId} stage=${diagnostic.stage} event=#${diagnostic.eventIndex}`,
      );
    }
    lines.push(
      `  cleanup=${scenario.cleanup.clean ? "PASS" : "FAIL"} residual=${scenario.cleanup.residualCount} closeEvents=${scenario.cleanup.closeEventCount} strongRefs=${scenario.liveness.strongFrameReferencesInTracker}`,
    );
  }
  lines.push(
    `\nTOTAL frames=${data.summary.framesCreated} closed=${data.summary.framesClosed} residual=${data.summary.residualFrames} diagnostics=${data.summary.diagnostics}`,
  );
  return lines.join("\n");
}
