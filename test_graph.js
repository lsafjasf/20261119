const assert = require("assert");
const { DATASETS } = require("./data.js");
const G = require("./graph.js");

// ---- 正常 GOP ----
{
  const { frames } = DATASETS.normal;
  const g = G.buildGraph(frames);
  assert.strictEqual(g.unresolved.length, 0, "正常 GOP 不应有悬空引用");

  const { order, hasCycle } = G.topoOrder(frames, g.deps);
  assert.strictEqual(hasCycle, false);
  assert.strictEqual(order.length, frames.length);
  // 拓扑序必须满足：每个参考帧都在其依赖者之前
  const pos = new Map(order.map((p, i) => [p, i]));
  for (const f of frames)
    for (const r of g.deps.get(f.poc))
      assert.ok(pos.get(r) < pos.get(f.poc), `POC ${r} 应在 ${f.poc} 前解码`);

  // 声明的 decodeIdx 必须与依赖约束一致
  assert.deepStrictEqual(G.checkDecodeOrder(frames, g.deps), []);

  assert.deepStrictEqual(G.findCycles(frames, g.deps), []);
  assert.deepStrictEqual(G.findOrphans(frames, g.deps), []);

  // 删除 POC 3 (P 帧)：传递影响应包含 1,2,4,5,6,7,8（6 依赖 3，7/8 依赖 6）
  const impact = G.transitiveImpact(3, g.dependents);
  assert.deepStrictEqual([...impact].sort((a, b) => a - b), [1, 2, 4, 5, 6, 7, 8]);
  // 删除 POC 9：只影响 7,8
  assert.deepStrictEqual([...G.transitiveImpact(9, g.dependents)].sort((a, b) => a - b), [7, 8]);
  // 删除叶子 B 帧：无影响
  assert.strictEqual(G.transitiveImpact(1, g.dependents).size, 0);
}

// ---- 异常 GOP ----
{
  const { frames } = DATASETS.broken;
  const g = G.buildGraph(frames);

  // 悬空引用：POC 2 的 RefPicList1[0] -> 99
  assert.strictEqual(g.unresolved.length, 1);
  assert.deepStrictEqual(g.unresolved[0], { fromPoc: 2, targetPoc: 99, field: "RefPicList1[0]" });

  // 环：3 <-> 5
  const cycles = G.findCycles(frames, g.deps);
  assert.strictEqual(cycles.length, 1);
  assert.deepStrictEqual([...cycles[0]].sort((a, b) => a - b), [3, 5]);

  // 拓扑序在有环时应不完整
  const { order, hasCycle } = G.topoOrder(frames, g.deps);
  assert.strictEqual(hasCycle, true);
  assert.ok(!order.includes(3) && !order.includes(5));

  // 孤儿帧：POC 7
  assert.deepStrictEqual(G.findOrphans(frames, g.deps), [7]);

  // decodeIdx 违规：POC 3 (decodeIdx 1) 依赖 POC 5 (decodeIdx 5)，参考帧排在后面
  const v = G.checkDecodeOrder(frames, g.deps);
  assert.ok(v.some((x) => x.framePoc === 3 && x.refPoc === 5));

  // 删除 POC 3：传递影响含 1,4,5,6,8（5 与 3 成环互相依赖，6 依赖 3，4/8 依赖 3/6）
  const impact = G.transitiveImpact(3, g.dependents);
  assert.deepStrictEqual([...impact].sort((a, b) => a - b), [1, 4, 5, 6, 8]);
}

console.log("全部断言通过 ✔");
