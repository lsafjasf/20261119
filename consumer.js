// consumer.js — 消费 Worker：按实时节拍取块。缺数据时输出明确的静音标记
// （seq = -1、全零样本、UNDERRUN 计数 +1），绝不读未提交样本。
// 每块用序号 + 样本值模式校验：证明无重读、无乱序、无未计数丢失、无撕裂读。
import { CTRL, dataViews, consumerReadBlock, verifyBlock } from './ring-core.js';
import { getPort } from './worker-port.js';

const port = await getPort();

let ctrl = null;
let views = null;
let timer = null;
let read = 0;
let silences = 0;
let gaps = 0;
let laps = 0;          // 读索引绕回圈数
let lastLap = 0;
const errors = [];     // 校验错误（保留最近 50 条）
const events = [];     // 待上报事件
const state = { expectedSeq: 0, totalGap: 0 };

function tickMs() {
  const blockLen = Atomics.load(ctrl, CTRL.BLOCK_LEN);
  const rate = Atomics.load(ctrl, CTRL.SAMPLE_RATE);
  return Math.max(1, (blockLen / rate) * 1000);
}

function schedule() {
  timer = setTimeout(tick, tickMs());
}

function pushEvent(kind, text) {
  events.push({ kind, text, t: Date.now() });
  if (events.length > 200) events.splice(0, events.length - 200);
}

function tick() {
  const block = consumerReadBlock(ctrl, views);
  if (block.kind === 'data') {
    read += 1;
    const droppedNow = Atomics.load(ctrl, CTRL.DROPPED);
    const producerSeqNow = Atomics.load(ctrl, CTRL.PRODUCER_SEQ);
    if (block.seq > state.expectedSeq) {
      gaps += 1;
      pushEvent('gap', `序号缺口 ${state.expectedSeq}..${block.seq - 1}` +
        `（${block.seq - state.expectedSeq} 块，与 DROPPED 对账中）`);
    }
    for (const err of verifyBlock(block, state, droppedNow, producerSeqNow)) {
      errors.push(err);
      if (errors.length > 50) errors.shift();
      pushEvent('error', err);
    }
    const lap = Math.floor(Atomics.load(ctrl, CTRL.READ) /
      Atomics.load(ctrl, CTRL.CAPACITY));
    if (lap > lastLap) {
      lastLap = lap;
      laps = lap;
      pushEvent('lap', `环形缓冲绕回：读索引进入第 ${lap + 1} 圈`);
    }
  } else if (block.kind === 'silence') {
    silences += 1;
    // 明确的静音标记：不消费任何槽位，输出全零块
    pushEvent('silence', `SILENCE 静音块 #${silences}（seq=-1，${block.blockLen} 点全零）`);
  }
  schedule();
}

setInterval(() => {
  if (!ctrl) return;
  port.postMessage({
    type: 'stats',
    read,
    silences,
    gaps,
    totalGap: state.totalGap,
    laps,
    expectedSeq: state.expectedSeq,
    paused: Atomics.load(ctrl, CTRL.PAUSE_CONSUMER) !== 0,
    errors: errors.slice(),
    events: events.splice(0, events.length),
  });
}, 200);

port.onMessage((msg) => {
  if (msg.type === 'init') {
    ctrl = new Int32Array(msg.ctrlSab);
    views = dataViews(msg.dataSab);
    schedule();
    port.postMessage({ type: 'ready' });
  } else if (msg.type === 'stop') {
    clearTimeout(timer);
  }
});
