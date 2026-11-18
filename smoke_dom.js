// 冒烟测试：用最小 DOM 桩在 Node 中运行 app.js 的渲染路径
function makeEl(tag) {
  return {
    tag, children: [], attrs: {}, listeners: {}, _html: "", textContent: "",
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k]; },
    appendChild(c) { this.children.push(c); return c; },
    addEventListener(ev, fn) { this.listeners[ev] = fn; },
    set innerHTML(v) { this._html = v; this.children = []; },
    get innerHTML() { return this._html; },
    set className(v) { this.attrs.class = v; },
    set onclick(fn) { this.listeners.click = fn; },
  };
}
const ids = {};
for (const id of ["graph", "reportBody", "detailBody", "datasetSelect"])
  ids[id] = makeEl(id);
ids.datasetSelect.value = "normal";

global.document = {
  getElementById: (id) => (ids[id] ||= makeEl(id)),
  createElementNS: (_ns, tag) => makeEl(tag),
  createElement: (tag) => makeEl(tag),
};

const { DATASETS } = require("./data.js");
Object.assign(global, require("./graph.js"));
global.DATASETS = DATASETS;

require("fs");
const src = require("fs").readFileSync("./app.js", "utf8");
eval(src);

// 初始渲染（normal）
if (!ids.reportBody._html.includes("无依赖环")) throw new Error("normal 报告异常");
if (!ids.detailBody._html.includes("点击图中任意帧")) throw new Error("初始详情异常");

// 模拟点击 POC 3 节点
const svgKids = ids.graph.children.filter((c) => c.tag === "g");
if (svgKids.length !== DATASETS.normal.frames.length) throw new Error("节点数不符");
const node3 = svgKids.find((g) => String(g.attrs["data-poc"]) === "3");
node3.listeners.click();
if (!ids.detailBody._html.includes("P3")) throw new Error("选中详情异常");

// 模拟点击“模拟删除该帧”按钮
ids.delBtn.listeners.click();
if (!ids.detailBody._html.includes("传递影响")) throw new Error("删除影响展示异常");
if (!ids.detailBody._html.includes("传递影响（7 帧）")) throw new Error("传递影响数量异常");

// 切换到异常数据集
ids.datasetSelect.value = "broken";
ids.datasetSelect.listeners.change();
if (!ids.reportBody._html.includes("依赖环")) throw new Error("环报告缺失");
if (!ids.reportBody._html.includes("孤儿帧")) throw new Error("孤儿帧报告缺失");
if (!ids.reportBody._html.includes("RefPicList1[0]")) throw new Error("悬空引用来源字段缺失");
if (!ids.reportBody._html.includes("解码顺序违规")) throw new Error("顺序违规报告缺失");

console.log("DOM 冒烟测试通过 ✔");
