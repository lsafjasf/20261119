// ring-core.js — 单生产者单消费者（SPSC）环形缓冲协议核心。
// 浏览器 Worker 与 Node worker_threads 共用，无任何环境依赖。
//
// 内存布局：
//   控制块（Int32Array on SharedArrayBuffer）：单调递增的读/写索引等。
//   数据区（SharedArrayBuffer）：capacity 个定长槽位，每槽 = 8 字节 Float64 序号头
//   + MAX_BLOCK_LEN 个 Float32 采样。槽定长，改块长无需重新分配。
//
// 提交协议（关键）：
//   生产者先写完整块（序号头 + 全部采样），最后才 Atomics.store 提交写索引；
//   消费者只读取“读索引 < 已提交写索引”的槽位，读完后 Atomics.store 提交读索引。
//   因此消费者永远读不到未提交（写了一半）的样本。

export const CTRL = {
  WRITE: 0,          // 写索引：已提交块总数（单调递增，槽位 = WRITE % CAPACITY）
  READ: 1,           // 读索引：已消费块总数
  CAPACITY: 2,       // 环容量（块数）
  BLOCK_LEN: 3,      // 当前块长（采样点/块）
  PAUSE_PRODUCER: 4, // 非 0 时生产者暂停写入
  PAUSE_CONSUMER: 5, // 非 0 时消费者暂停读取
  DROPPED: 6,        // 生产者因满缓冲丢弃的块数（单调递增）
  UNDERRUN: 7,       // 消费者缺数据、输出静音标记的次数
  GENERATION: 8,     // 配置代际：每次改块长/复位时 +1
  SAMPLE_RATE: 9,
  PRODUCER_SEQ: 10,  // 生产者已推进到的序号（下一个待写块序号）
  CTRL_INTS: 16,
};

export const MAX_BLOCK_LEN = 1024;
export const HEADER_BYTES = 8;                              // Float64 序号
export const SLOT_BYTES = HEADER_BYTES + MAX_BLOCK_LEN * 4; // 4104，8 字节对齐
// 样本值 = Math.fround(seq * SEQ_STRIDE + i)，SEQ_STRIDE > MAX_BLOCK_LEN 保证跨块不撞值
export const SEQ_STRIDE = 4096;

export function createRing(capacityBlocks, sampleRate, blockLen) {
  const ctrlSab = new SharedArrayBuffer(CTRL.CTRL_INTS * 4);
  const dataSab = new SharedArrayBuffer(capacityBlocks * SLOT_BYTES);
  const ctrl = new Int32Array(ctrlSab);
  Atomics.store(ctrl, CTRL.WRITE, 0);
  Atomics.store(ctrl, CTRL.READ, 0);
  Atomics.store(ctrl, CTRL.CAPACITY, capacityBlocks);
  Atomics.store(ctrl, CTRL.BLOCK_LEN, blockLen);
  Atomics.store(ctrl, CTRL.SAMPLE_RATE, sampleRate);
  return { ctrlSab, dataSab };
}

// 数据区视图：f64 读序号头，f32 读采样。
export function dataViews(dataSab) {
  return {
    f64: new Float64Array(dataSab),
    f32: new Float32Array(dataSab),
  };
}

function slotOffset(slot) {
  return slot * SLOT_BYTES;
}

// 生产者：尝试写入一块。返回 'written' | 'full' | 'paused'。
export function producerWriteBlock(ctrl, views, seq) {
  if (Atomics.load(ctrl, CTRL.PAUSE_PRODUCER) !== 0) return 'paused';
  const capacity = Atomics.load(ctrl, CTRL.CAPACITY);
  const blockLen = Atomics.load(ctrl, CTRL.BLOCK_LEN);
  const write = Atomics.load(ctrl, CTRL.WRITE);
  const read = Atomics.load(ctrl, CTRL.READ);
  if (write - read >= capacity) {
    Atomics.add(ctrl, CTRL.DROPPED, 1); // 满缓冲：丢弃本块并计数（实时源不可阻塞）
    Atomics.store(ctrl, CTRL.PRODUCER_SEQ, seq + 1);
    return 'full';
  }
  const slot = write % capacity;
  const off = slotOffset(slot);
  const base = seq * SEQ_STRIDE;
  const f32Start = (off + HEADER_BYTES) / 4;
  for (let i = 0; i < blockLen; i++) {
    views.f32[f32Start + i] = base + i; // 写入 Float32 时隐式 fround
  }
  views.f64[off / 8] = seq;                   // 序号头最后写
  Atomics.store(ctrl, CTRL.WRITE, write + 1); // 提交：至此本块才对消费者可见
  Atomics.store(ctrl, CTRL.PRODUCER_SEQ, seq + 1);
  return 'written';
}

// 消费者：尝试读取一块。
// 返回 { kind: 'data', seq, blockLen, samples } | { kind: 'silence', ... } | { kind: 'paused' }
// samples 为复制出来的 Float32Array，校验时不触碰共享内存。
export function consumerReadBlock(ctrl, views) {
  if (Atomics.load(ctrl, CTRL.PAUSE_CONSUMER) !== 0) return { kind: 'paused' };
  const blockLen = Atomics.load(ctrl, CTRL.BLOCK_LEN);
  const write = Atomics.load(ctrl, CTRL.WRITE);
  const read = Atomics.load(ctrl, CTRL.READ);
  if (read >= write) {
    Atomics.add(ctrl, CTRL.UNDERRUN, 1);
    return { kind: 'silence', seq: -1, blockLen, samples: new Float32Array(blockLen) };
  }
  const slot = read % Atomics.load(ctrl, CTRL.CAPACITY);
  const off = slotOffset(slot);
  const seq = views.f64[off / 8];
  const f32Start = (off + HEADER_BYTES) / 4;
  const samples = views.f32.slice(f32Start, f32Start + blockLen);
  Atomics.store(ctrl, CTRL.READ, read + 1); // 提交读索引，槽位可被生产者复用
  return { kind: 'data', seq, blockLen, samples };
}

// 主线程：改块长 / 复位。调用前需已暂停两端并等待在途节拍结束。
// 丢弃在途块时计入 DROPPED，保持“序号缺口 == 丢弃计数增量”这一不变量。
export function reconfigure(ctrl, newBlockLen) {
  const write = Atomics.load(ctrl, CTRL.WRITE);
  const read = Atomics.load(ctrl, CTRL.READ);
  const inflight = write - read;
  if (inflight > 0) Atomics.add(ctrl, CTRL.DROPPED, inflight);
  Atomics.store(ctrl, CTRL.WRITE, 0);
  Atomics.store(ctrl, CTRL.READ, 0);
  Atomics.store(ctrl, CTRL.BLOCK_LEN, newBlockLen);
  Atomics.add(ctrl, CTRL.GENERATION, 1);
  return inflight;
}

// 消费者侧校验：返回错误描述数组（空 = 通过）。
// state: { expectedSeq, totalGap } 由调用方持有并原地更新。
// 对账不变量（按序号因果序成立）：
//   0 <= droppedNow - totalGap <= producerSeqNow - 1 - seq
// 即：当前块之前跳过的序号必然已被 DROPPED 计数（否则 = 未计数丢失）；
// 尚未观察到缺口的丢弃，必然对应本块之后生产的序号（否则 = 计数越界）。
// 系统静止（生产者暂停且缓冲排空）时，totalGap === DROPPED 精确成立。
export function verifyBlock(block, state, droppedNow, producerSeqNow) {
  const errors = [];
  const seq = block.seq;
  if (seq < state.expectedSeq) {
    errors.push(`乱序/重读：收到 seq=${seq}，期望 >= ${state.expectedSeq}`);
  } else if (seq > state.expectedSeq) {
    state.totalGap += seq - state.expectedSeq;
  }
  const unaccounted = droppedNow - state.totalGap;
  if (unaccounted < 0) {
    errors.push(`缺口未计数：累计缺口 ${state.totalGap} 超出 DROPPED=${droppedNow}`);
  } else if (unaccounted > producerSeqNow - 1 - seq) {
    errors.push(`丢弃计数越界：未对账丢弃 ${unaccounted}，` +
      `但本块之后仅生产 ${Math.max(0, producerSeqNow - 1 - seq)} 块`);
  }
  const base = seq * SEQ_STRIDE;
  for (let i = 0; i < block.blockLen; i++) {
    if (block.samples[i] !== Math.fround(base + i)) {
      errors.push(`样本损坏/读到未提交数据：seq=${seq} 第 ${i} 点，` +
        `期望 ${Math.fround(base + i)}，实际 ${block.samples[i]}`);
      break; // 每块只报第一处
    }
  }
  state.expectedSeq = seq + 1;
  return errors;
}
