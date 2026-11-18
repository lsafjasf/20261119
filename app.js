// 交互逻辑：渲染依赖图、帧详情、结构报告与删除模拟
const svg = document.getElementById("graph");
const reportBody = document.getElementById("reportBody");
const detailBody = document.getElementById("detailBody");
const datasetSelect = document.getElementById("datasetSelect");

const TYPE_COLOR = { I: "#34c98e", P: "#4f9cf9", B: "#f5a742" };
const SVG_NS = "http://www.w3.org/2000/svg";

let state = null; // { frames, graph, topo, cycles, orphans, violations, selected, impact }

function el(tag, attrs = {}, parent) {
  const n = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}

function loadDataset(key) {
  const { frames } = DATASETS[key];
  const graph = buildGraph(frames);
  const topo = topoOrder(frames, graph.deps);
  state = {
    frames, graph, topo,
    cycles: findCycles(frames, graph.deps),
    orphans: findOrphans(frames, graph.deps),
    violations: checkDecodeOrder(frames, graph.deps),
    selected: null, impact: null, deleted: null,
  };
  renderAll();
}

function renderAll() {
  renderGraph();
  renderReports();
  renderDetail();
}

// 布局：x 按显示顺序 POC，y 按帧类型分行（锚帧上行，B 帧下行）
function layout(frames) {
  const sorted = [...frames].sort((a, b) => a.poc - b.poc);
  const pos = new Map();
  const w = Math.max(720, sorted.length * 92 + 80);
  sorted.forEach((f, i) => {
    const x = 70 + i * 92;
    const y = f.type === "B" ? 250 : 120;
    pos.set(f.poc, { x, y });
  });
  return { pos, w, h: 360 };
}

function renderGraph() {
  svg.innerHTML = "";
  const { frames, graph, selected, impact, deleted } = state;
  const { pos, w, h } = layout(frames);
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);

  const defs = el("defs", {}, svg);
  for (const [id, color] of [["arrow", "#54648a"], ["arrowRed", "#ff5d6c"]]) {
    const m = el("marker", {
      id, viewBox: "0 0 10 10", refX: 9, refY: 5,
      markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse",
    }, defs);
    el("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: color }, m);
  }

  const cycleSet = new Set(state.cycles.flat());
  const orphanSet = new Set(state.orphans);
  const refSet = new Set();   // 选中帧的参考帧
  const depSet = new Set();   // 选中帧的直接被依赖帧
  if (selected !== null) {
    for (const r of graph.deps.get(selected)) refSet.add(r);
    for (const d of graph.dependents.get(selected)) depSet.add(d);
  }

  // 边：帧 -> 其参考帧（箭头指向参考帧）
  for (const f of frames) {
    if (f.poc === deleted) continue;
    const a = pos.get(f.poc);
    for (const r of f.refs) {
      const b = pos.get(r.poc);
      if (!b) { // 悬空引用：画向缺失位置的红色虚线
        el("line", {
          x1: a.x, y1: a.y, x2: a.x + 46, y2: a.y - 64, class: "edge unresolved",
        }, svg);
        const t = el("text", { x: a.x + 50, y: a.y - 68, fill: "#ff5d6c", "font-size": 11 }, svg);
        t.textContent = `POC ${r.poc}?`;
        continue;
      }
      if (r.poc === deleted) continue;
      let cls = "edge";
      if (impact && (impact.has(f.poc) || impact.has(r.poc))) cls += " impact";
      else if (f.poc === selected) cls += " ref";
      else if (r.poc === selected) cls += " dep";
      const mid = (a.y + b.y) / 2 + (a.y === b.y ? -28 : 0);
      el("path", {
        d: `M ${a.x} ${a.y} Q ${(a.x + b.x) / 2} ${mid} ${b.x} ${b.y}`,
        class: cls,
      }, svg);
    }
  }

  // 节点
  for (const f of frames) {
    if (f.poc === deleted) continue;
    const { x, y } = pos.get(f.poc);
    const g = el("g", { class: "node", "data-poc": f.poc }, svg);
    let cls = "node";
    if (f.poc === selected) cls += " selected";
    if (cycleSet.has(f.poc)) cls += " cycle";
    if (orphanSet.has(f.poc)) cls += " orphan";
    if (impact && impact.has(f.poc)) cls += " impact";
    if (selected !== null && f.poc !== selected &&
        !refSet.has(f.poc) && !depSet.has(f.poc) && !(impact && impact.has(f.poc)))
      cls += " dim";
    g.setAttribute("class", cls);
    el("circle", { cx: x, cy: y, r: 22, fill: TYPE_COLOR[f.type], stroke: "#2a3550" }, g);
    const label = el("text", { x, y: y + 4 }, g);
    label.textContent = `${f.type}${f.poc}`;
    const sub = el("text", { x, y: y + 40, class: "poc-label" }, g);
    sub.textContent = `解码#${f.decodeIdx}`;
    if (cycleSet.has(f.poc)) {
      const b = el("text", { x: x + 18, y: y - 24, class: "badge", fill: "#ff5d6c" }, g);
      b.textContent = "⟳";
    }
    if (orphanSet.has(f.poc)) {
      const b = el("text", { x: x - 24, y: y - 24, class: "badge", fill: "#9aa5b8" }, g);
      b.textContent = "⚠";
    }
    g.addEventListener("click", () => {
      state.selected = state.selected === f.poc ? null : f.poc;
      state.impact = null; state.deleted = null;
      renderAll();
    });
  }
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function renderReports() {
  const { graph, topo, cycles, orphans, violations } = state;
  const parts = [];

  if (cycles.length === 0) parts.push(`<p class="ok">✔ 无依赖环</p>`);
  else parts.push(
    `<p class="err">✖ 检测到 ${cycles.length} 个依赖环：</p><ul>` +
    cycles.map((c) => `<li>环: ${c.map((p) => `POC ${p}`).join(" ↔ ")}</li>`).join("") +
    `</ul>`);

  if (orphans.length === 0) parts.push(`<p class="ok">✔ 无孤儿帧</p>`);
  else parts.push(
    `<p class="err">✖ 孤儿帧（非 IDR 且无参考帧）:</p><ul>` +
    orphans.map((p) => `<li>POC ${p}</li>`).join("") + `</ul>`);

  if (graph.unresolved.length === 0) parts.push(`<p class="ok">✔ 所有引用均可解析</p>`);
  else parts.push(
    `<p class="err">✖ ${graph.unresolved.length} 个无法解析的引用：</p><ul>` +
    graph.unresolved.map((u) =>
      `<li>帧 POC ${u.fromPoc} 的 <code>${esc(u.field)}</code> → POC ${u.targetPoc}（目标不存在）</li>`
    ).join("") + `</ul>`);

  if (violations.length === 0)
    parts.push(`<p class="ok">✔ 声明解码顺序满足全部依赖约束</p>`);
  else parts.push(
    `<p class="err">✖ 解码顺序违规（参考帧排在依赖者之后）：</p><ul>` +
    violations.map((v) => `<li>POC ${v.framePoc} 依赖 POC ${v.refPoc}，却先解码</li>`).join("") +
    `</ul>`);

  const topoStr = topo.hasCycle
    ? `<span class="warn">存在环，无法给出完整拓扑序（可排序部分: ${topo.order.join(" → ")}）</span>`
    : `<code>${topo.order.join(" → ")}</code>`;
  parts.push(`<p>拓扑解码序: ${topoStr}</p>`);

  reportBody.innerHTML = parts.join("");
}

function renderDetail() {
  const { frames, graph, topo, selected, impact, deleted } = state;
  if (selected === null) {
    detailBody.innerHTML = `<p class="hint">点击图中任意帧查看详情</p>`;
    return;
  }
  const f = frames.find((x) => x.poc === selected);
  const refs = f.refs.map((r) => {
    const ok = graph.byPoc.has(r.poc);
    return `<li>${ok ? `POC ${r.poc}` : `<span class="err">POC ${r.poc}（无法解析）</span>`} ` +
      `<code>${esc(r.field)}</code></li>`;
  }).join("") || "<li>无</li>";
  const deps = [...graph.dependents.get(selected)].sort((a, b) => a - b);
  const topoPos = topo.order.indexOf(selected);

  let html = `
    <p><strong>${f.type}${f.poc}</strong>（POC ${f.poc}，类型 ${f.type}${f.isIDR ? "，IDR" : ""}）</p>
    <p>声明解码序号: <code>#${f.decodeIdx}</code>；
       拓扑解码位置: ${topoPos >= 0 ? `<code>#${topoPos}</code>` : `<span class="err">处于环中，无法排序</span>`}</p>
    <p>参考帧（它依赖谁）:</p><ul>${refs}</ul>
    <p>被依赖帧（谁依赖它）:</p><ul>${deps.length ? deps.map((d) => `<li>POC ${d}</li>`).join("") : "<li>无</li>"}</ul>
  `;

  if (deleted === selected && impact) {
    const list = [...impact].sort((a, b) => a - b);
    html += `<p class="err">删除该帧的传递影响（${list.length} 帧）:</p><ul>` +
      (list.length
        ? list.map((p) => {
            const tf = frames.find((x) => x.poc === p);
            return `<li>POC ${p}（${tf.type}${p}，解码#${tf.decodeIdx}）</li>`;
          }).join("")
        : "<li>无其他帧受影响</li>") +
      `</ul><p class="hint">影响沿“被依赖”边传递闭包计算，非仅直接邻居。</p>`;
  }
  html += `<button class="danger" id="delBtn">模拟删除该帧</button>`;
  if (deleted !== null) html += `<button class="ghost" id="undoBtn">撤销删除</button>`;
  detailBody.innerHTML = html;

  document.getElementById("delBtn").onclick = () => {
    state.deleted = selected;
    state.impact = transitiveImpact(selected, graph.dependents);
    renderAll();
  };
  const undo = document.getElementById("undoBtn");
  if (undo) undo.onclick = () => { state.deleted = null; state.impact = null; renderAll(); };
}

// 初始化
for (const [key, ds] of Object.entries(DATASETS)) {
  const opt = document.createElement("option");
  opt.value = key; opt.textContent = ds.name;
  datasetSelect.appendChild(opt);
}
datasetSelect.addEventListener("change", () => loadDataset(datasetSelect.value));
loadDataset("normal");
