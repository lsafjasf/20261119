// 依赖图核心算法（纯函数，浏览器与 Node 均可用）

// 构建图：解析引用，返回邻接表与无法解析的引用列表
function buildGraph(frames) {
  const byPoc = new Map(frames.map((f) => [f.poc, f]));
  const deps = new Map();     // poc -> Set(它依赖的 poc)
  const dependents = new Map(); // poc -> Set(依赖它的 poc)
  const unresolved = [];      // 无法解析的引用
  for (const f of frames) {
    deps.set(f.poc, new Set());
    dependents.set(f.poc, new Set());
  }
  for (const f of frames) {
    for (const r of f.refs) {
      if (!byPoc.has(r.poc)) {
        unresolved.push({ fromPoc: f.poc, targetPoc: r.poc, field: r.field });
        continue;
      }
      deps.get(f.poc).add(r.poc);
      dependents.get(r.poc).add(f.poc);
    }
  }
  return { byPoc, deps, dependents, unresolved };
}

// Kahn 拓扑排序：保证所有参考帧排在依赖者之前（合法解码顺序）
// 返回 { order, hasCycle }；有环时 order 只含可排序部分
function topoOrder(frames, deps) {
  const indeg = new Map();
  for (const f of frames) indeg.set(f.poc, deps.get(f.poc).size);
  const queue = frames.filter((f) => indeg.get(f.poc) === 0).map((f) => f.poc);
  queue.sort((a, b) => a - b);
  const dependents = new Map(frames.map((f) => [f.poc, []]));
  for (const f of frames)
    for (const r of deps.get(f.poc)) dependents.get(r).push(f.poc);
  const order = [];
  while (queue.length) {
    const p = queue.shift();
    order.push(p);
    for (const d of dependents.get(p)) {
      indeg.set(d, indeg.get(d) - 1);
      if (indeg.get(d) === 0) queue.push(d);
    }
  }
  return { order, hasCycle: order.length < frames.length };
}

// 核对码流声明的 decodeIdx 是否满足依赖约束
// 返回违规列表 [{ framePoc, refPoc }]
function checkDecodeOrder(frames, deps) {
  const violations = [];
  for (const f of frames) {
    for (const r of deps.get(f.poc)) {
      const ref = frames.find((x) => x.poc === r);
      if (ref.decodeIdx >= f.decodeIdx)
        violations.push({ framePoc: f.poc, refPoc: r });
    }
  }
  return violations;
}

// 删除某帧后的传递影响：沿 dependents 做 BFS 闭包
// 结果不含被删帧本身（环中可能绕回自身，需排除）
function transitiveImpact(poc, dependents) {
  const seen = new Set();
  const queue = [...(dependents.get(poc) || [])];
  while (queue.length) {
    const cur = queue.shift();
    if (seen.has(cur) || cur === poc) continue;
    seen.add(cur);
    for (const n of dependents.get(cur) || []) queue.push(n);
  }
  return seen;
}

// Tarjan 强连通分量，找出所有依赖环（含自环）
function findCycles(frames, deps) {
  const index = new Map(), low = new Map(), onStack = new Set();
  const stack = [], sccs = [];
  let counter = 0;
  function strongconnect(p) {
    index.set(p, counter); low.set(p, counter); counter++;
    stack.push(p); onStack.add(p);
    for (const r of deps.get(p)) {
      if (!index.has(r)) { strongconnect(r); low.set(p, Math.min(low.get(p), low.get(r))); }
      else if (onStack.has(r)) low.set(p, Math.min(low.get(p), index.get(r)));
    }
    if (low.get(p) === index.get(p)) {
      const scc = [];
      let w;
      do { w = stack.pop(); onStack.delete(w); scc.push(w); } while (w !== p);
      if (scc.length > 1 || deps.get(p).has(p)) sccs.push(scc);
    }
  }
  for (const f of frames) if (!index.has(f.poc)) strongconnect(f.poc);
  return sccs;
}

// 孤儿帧：非 IDR 且解析后没有任何参考帧（无法被解码）
function findOrphans(frames, deps) {
  return frames
    .filter((f) => !f.isIDR && deps.get(f.poc).size === 0)
    .map((f) => f.poc);
}

const GraphLib = { buildGraph, topoOrder, checkDecodeOrder, transitiveImpact, findCycles, findOrphans };
if (typeof module !== "undefined") module.exports = GraphLib;
