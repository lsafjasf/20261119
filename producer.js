// producer.js — 生产 Worker：按实时节拍向环形缓冲写入带递增序号的采样块。
// 满缓冲时丢弃并计数（实时音频源不可阻塞）；暂停标志由主线程经 Atomics 控制。
import { CTRL, dataViews, producerWriteBlock } from './ring-core.js';
import { getPort } from './worker-port.js';

const port = await getPort();

let ctrl = null;
let views = null;
let timer = null;
let seq = 0;        // 块序号，跨改块长/复位持续递增，保证全局连续性可校验
let written = 0;
let dropped = 0;

function tickMs() {
  const blockLen = Atomics.load(ctrl, CTRL.BLOCK_LEN);
  const rate = Atomics.load(ctrl, CTRL.SAMPLE_RATE);
  return Math.max(1, (blockLen / rate) * 1000);
}

function schedule() {
  timer = setTimeout(tick, tickMs());
}

function tick() {
  const result = producerWriteBlock(ctrl, views, seq);
  if (result === 'written') {
    seq += 1;
    written += 1;
  } else if (result === 'full') {
    seq += 1; // 被丢弃的块同样消耗序号，消费者据此核对缺口
    dropped += 1;
  }
  schedule();
}

setInterval(() => {
  if (!ctrl) return;
  port.postMessage({
    type: 'stats',
    written,
    dropped,
    nextSeq: seq,
    paused: Atomics.load(ctrl, CTRL.PAUSE_PRODUCER) !== 0,
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
