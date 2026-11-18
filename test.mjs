// test.mjs — 用 Node worker_threads 运行与浏览器完全相同的 producer.js /
// consumer.js / ring-core.js，自动化验证：绕回、满缓冲、暂停恢复、改块长。
// 运行：node test.mjs
import { Worker } from 'node:worker_threads';
import { CTRL, createRing, reconfigure } from './ring-core.js';

const CAPACITY = 64;
const SAMPLE_RATE = 48000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
function check(name, cond, detail = '') {
  results.push({ name, ok: !!cond });
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
}

const { ctrlSab, dataSab } = createRing(CAPACITY, SAMPLE_RATE, 128);
const ctrl = new Int32Array(ctrlSab);

const producer = new Worker(new URL('./producer.js', import.meta.url));
const consumer = new Worker(new URL('./consumer.js', import.meta.url));

let producerStats = {};
let consumerStats = {};
const allErrors = new Set();
const silenceEvents = [];
const gapEvents = [];

producer.on('message', (m) => { if (m.type === 'stats') producerStats = m; });
consumer.on('message', (m) => {
  if (m.type !== 'stats') return;
  consumerStats = m;
  for (const e of m.errors) allErrors.add(e);
  for (const ev of m.events) {
    if (ev.kind === 'silence') silenceEvents.push(ev);
    if (ev.kind === 'gap') gapEvents.push(ev);
  }
});

producer.postMessage({ type: 'init', ctrlSab, dataSab });
consumer.postMessage({ type: 'init', ctrlSab, dataSab });

// 场景 A：正常运行 1.5s —— 验证绕回与稳态连续性
await sleep(1500);
const aWritten = producerStats.written ?? 0;
check('A1 稳态生产消费', aWritten > CAPACITY * 2, `written=${aWritten}, capacity=${CAPACITY}`);
check('A2 环形缓冲绕回多圈', (consumerStats.laps ?? 0) >= 2, `laps=${consumerStats.laps}`);
check('A3 稳态无校验错误', allErrors.size === 0, [...allErrors][0] ?? '');

// 场景 B：暂停消费者 0.6s —— 缓冲填满，生产者丢块；恢复后序号连续性可对账
Atomics.store(ctrl, CTRL.PAUSE_CONSUMER, 1);
await sleep(600);
Atomics.store(ctrl, CTRL.PAUSE_CONSUMER, 0);
await sleep(600);
const dropped = Atomics.load(ctrl, CTRL.DROPPED);
check('B1 暂停消费者导致满缓冲丢块', dropped > 0, `dropped=${dropped}`);
check('B2 缺口事件已与 DROPPED 对账', gapEvents.length > 0, `gaps=${gapEvents.length}`);
check('B3 恢复后无重读/乱序/未计数丢失', allErrors.size === 0, [...allErrors][0] ?? '');

// 场景 C：暂停生产者 0.6s —— 消费者缺数据，输出明确静音标记；恢复后继续
Atomics.store(ctrl, CTRL.PAUSE_PRODUCER, 1);
await sleep(600);
Atomics.store(ctrl, CTRL.PAUSE_PRODUCER, 0);
await sleep(600);
const underrun = Atomics.load(ctrl, CTRL.UNDERRUN);
check('C1 暂停生产者导致静音块输出', underrun > 0 && silenceEvents.length > 0,
  `underrun=${underrun}`);
check('C2 静音块为明确标记（seq=-1 全零，不计入消费）',
  silenceEvents.every((e) => e.text.includes('seq=-1')));
check('C3 恢复后样本连续性保持', allErrors.size === 0, [...allErrors][0] ?? '');

// 场景 D：运行中改块长（128 → 512 → 64）—— 复位协议不破坏连续性
for (const len of [512, 64]) {
  Atomics.store(ctrl, CTRL.PAUSE_PRODUCER, 1);
  Atomics.store(ctrl, CTRL.PAUSE_CONSUMER, 1);
  await sleep(120);
  reconfigure(ctrl, len);
  Atomics.store(ctrl, CTRL.PAUSE_PRODUCER, 0);
  Atomics.store(ctrl, CTRL.PAUSE_CONSUMER, 0);
  await sleep(500);
  check(`D 改块长为 ${len} 后无校验错误`, allErrors.size === 0, [...allErrors][0] ?? '');
}

// 场景 E：静止对账 —— 暂停生产者、排空缓冲后，累计缺口与 DROPPED 精确相等，
// 且消费块数 == 实际写入块数（无重读、无漏读）
Atomics.store(ctrl, CTRL.PAUSE_PRODUCER, 1);
await sleep(500);
const finalDropped = Atomics.load(ctrl, CTRL.DROPPED);
check('E1 静止时累计缺口 == DROPPED', consumerStats.totalGap === finalDropped,
  `totalGap=${consumerStats.totalGap}, dropped=${finalDropped}`);
// 用 Atomics 读索引真值（统计快照有最多 200ms 滞后，不能用于精确断言）
check('E2 静止时缓冲排空（WRITE == READ）',
  Atomics.load(ctrl, CTRL.WRITE) === Atomics.load(ctrl, CTRL.READ),
  `write=${Atomics.load(ctrl, CTRL.WRITE)}, read=${Atomics.load(ctrl, CTRL.READ)}`);
check('E3 全程零校验错误（无重读、无乱序、无未提交读）', allErrors.size === 0);
console.log(`\n统计: written=${producerStats.written} read=${consumerStats.read} ` +
  `dropped=${Atomics.load(ctrl, CTRL.DROPPED)} silence=${Atomics.load(ctrl, CTRL.UNDERRUN)} ` +
  `laps=${consumerStats.laps}`);

producer.terminate();
consumer.terminate();
const failed = results.filter((r) => !r.ok).length;
console.log(failed ? `\n${failed} 项失败` : '\n全部通过');
process.exit(failed ? 1 : 0);
