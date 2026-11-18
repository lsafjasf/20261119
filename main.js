// main.js — 主线程：建环、起 Worker、响应控制（暂停/改块长）、展示统计与事件。
import { CTRL, createRing, reconfigure } from './ring-core.js';

const CAPACITY = 64;      // 环容量（块）
const SAMPLE_RATE = 48000;

const $ = (id) => document.getElementById(id);
const els = {
  isolated: $('isolated'), start: $('btn-start'),
  pauseP: $('btn-pause-producer'), pauseC: $('btn-pause-consumer'),
  blockLen: $('sel-blocklen'),
  statWritten: $('stat-written'), statRead: $('stat-read'),
  statDropped: $('stat-dropped'), statSilence: $('stat-silence'),
  statGaps: $('stat-gaps'), statLaps: $('stat-laps'),
  statErrors: $('stat-errors'), statOccupancy: $('stat-occupancy'),
  occBar: $('occ-bar'), log: $('log'),
};

els.isolated.textContent = `crossOriginIsolated = ${self.crossOriginIsolated}`;
els.isolated.className = self.crossOriginIsolated ? 'ok' : 'bad';

let ctrl = null;
let ctrlSab = null;
let dataSab = null;
let producer = null;
let consumer = null;
let running = false;
let producerStats = {};
let consumerStats = {};

function log(kind, text) {
  const div = document.createElement('div');
  div.className = `log-${kind}`;
  div.textContent = `[${new Date().toLocaleTimeString('zh-CN', { hour12: false })}.` +
    `${String(Date.now() % 1000).padStart(3, '0')}] ${text}`;
  els.log.prepend(div);
  while (els.log.children.length > 120) els.log.lastChild.remove();
}

function setPaused(which, paused) {
  Atomics.store(ctrl, which, paused ? 1 : 0);
}

async function changeBlockLen(newLen) {
  // 协议：暂停两端 → 等待在途节拍结束 → 复位并改块长 → 恢复
  setPaused(CTRL.PAUSE_PRODUCER, true);
  setPaused(CTRL.PAUSE_CONSUMER, true);
  await new Promise((r) => setTimeout(r, 120));
  const inflight = reconfigure(ctrl, newLen);
  log('config', `块长改为 ${newLen}，复位环形缓冲（丢弃在途 ${inflight} 块，已计入 DROPPED）`);
  setPaused(CTRL.PAUSE_PRODUCER, false);
  setPaused(CTRL.PAUSE_CONSUMER, false);
  syncPauseButtons();
}

function syncPauseButtons() {
  els.pauseP.textContent = Atomics.load(ctrl, CTRL.PAUSE_PRODUCER) ? '恢复生产者' : '暂停生产者';
  els.pauseC.textContent = Atomics.load(ctrl, CTRL.PAUSE_CONSUMER) ? '恢复消费者' : '暂停消费者';
}

function start() {
  const blockLen = Number(els.blockLen.value);
  ({ ctrlSab, dataSab } = createRing(CAPACITY, SAMPLE_RATE, blockLen));
  ctrl = new Int32Array(ctrlSab);
  producer = new Worker('./producer.js', { type: 'module' });
  consumer = new Worker('./consumer.js', { type: 'module' });
  producer.onmessage = (e) => { if (e.data.type === 'stats') producerStats = e.data; };
  consumer.onmessage = (e) => {
    if (e.data.type !== 'stats') return;
    consumerStats = e.data;
    for (const ev of e.data.events) log(ev.kind, ev.text);
  };
  producer.postMessage({ type: 'init', ctrlSab, dataSab });
  consumer.postMessage({ type: 'init', ctrlSab, dataSab });
  running = true;
  els.start.textContent = '停止';
  els.pauseP.disabled = els.pauseC.disabled = els.blockLen.disabled = false;
  syncPauseButtons();
  log('config', `启动：容量 ${CAPACITY} 块，块长 ${blockLen}，采样率 ${SAMPLE_RATE} Hz`);
}

function stop() {
  producer?.terminate();
  consumer?.terminate();
  producer = consumer = null;
  running = false;
  els.start.textContent = '启动';
  els.pauseP.disabled = els.pauseC.disabled = els.blockLen.disabled = true;
  log('config', '已停止（统计保留）');
}

els.start.onclick = () => (running ? stop() : start());
els.pauseP.onclick = () => {
  setPaused(CTRL.PAUSE_PRODUCER, !Atomics.load(ctrl, CTRL.PAUSE_PRODUCER));
  log('config', els.pauseP.textContent.includes('暂停') ? '生产者已暂停' : '生产者已恢复');
  syncPauseButtons();
};
els.pauseC.onclick = () => {
  setPaused(CTRL.PAUSE_CONSUMER, !Atomics.load(ctrl, CTRL.PAUSE_CONSUMER));
  log('config', els.pauseC.textContent.includes('暂停') ? '消费者已暂停' : '消费者已恢复');
  syncPauseButtons();
};
els.blockLen.onchange = () => { if (running) changeBlockLen(Number(els.blockLen.value)); };

setInterval(() => {
  if (!ctrl) return;
  const write = Atomics.load(ctrl, CTRL.WRITE);
  const read = Atomics.load(ctrl, CTRL.READ);
  const occ = write - read;
  els.statWritten.textContent = producerStats.written ?? 0;
  els.statRead.textContent = consumerStats.read ?? 0;
  els.statDropped.textContent = Atomics.load(ctrl, CTRL.DROPPED);
  els.statSilence.textContent = Atomics.load(ctrl, CTRL.UNDERRUN);
  els.statGaps.textContent = consumerStats.gaps ?? 0;
  els.statLaps.textContent = consumerStats.laps ?? 0;
  const errCount = consumerStats.errors?.length ?? 0;
  els.statErrors.textContent = errCount;
  els.statErrors.className = errCount ? 'bad' : 'ok';
  els.statOccupancy.textContent = `${occ} / ${CAPACITY}`;
  els.occBar.style.width = `${(occ / CAPACITY) * 100}%`;
  els.occBar.className = occ >= CAPACITY ? 'bad' : occ > CAPACITY * 0.7 ? 'warn' : 'ok';
}, 100);

if (!self.crossOriginIsolated) {
  log('error', '页面未处于跨源隔离环境，SharedArrayBuffer 不可用。' +
    '请用 node server.js 启动（会带 COOP/COEP 响应头）。');
}
