function createNoise(seed = 0x4f31a2) {
  let state = seed >>> 0;
  return () => {
    state += 0x6d2b79f5;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296 - 0.5;
  };
}

export function createPhaseOffsetTone(sampleRate = 48000) {
  const frequency = 220;
  const expectedCycles = 935.25;
  const length = Math.floor((expectedCycles / frequency) * sampleRate);
  const data = new Float32Array(length);

  for (let i = 0; i < length; i += 1) {
    const phase = (2 * Math.PI * frequency * i) / sampleRate;
    data[i] = 0.56 * Math.sin(phase) + 0.08 * Math.sin(phase * 2);
  }

  return {
    name: "相位错开周期音 · 220 Hz",
    description: "稳定周期音，但首尾相差约四分之一周期。",
    source: "phase-tone",
    bpm: null,
    sampleRate,
    channels: [data]
  };
}

function addKick(data, sampleRate, time, gain = 0.82) {
  const start = Math.floor(time * sampleRate);
  const duration = Math.floor(sampleRate * 0.22);
  for (let i = 0; i < duration && start + i < data.length; i += 1) {
    const t = i / sampleRate;
    const envelope = Math.exp(-t * 24);
    const frequency = 132 * Math.exp(-t * 20) + 43;
    const phase = 2 * Math.PI * frequency * t;
    data[start + i] += Math.sin(phase) * envelope * gain;
  }
}

function addSnare(data, sampleRate, time, gain = 0.66) {
  const start = Math.floor(time * sampleRate);
  const duration = Math.floor(sampleRate * 0.16);
  const noise = createNoise(Math.floor(time * 10000) + 7);
  let previous = 0;
  for (let i = 0; i < duration && start + i < data.length; i += 1) {
    const t = i / sampleRate;
    const envelope = Math.exp(-t * 33);
    const raw = noise();
    const bright = raw - previous * 0.74;
    previous = raw;
    data[start + i] += (bright * 0.72 + Math.sin(2 * Math.PI * 184 * t) * 0.28) * envelope * gain;
  }
}

function addHat(data, sampleRate, time, gain = 0.15) {
  const start = Math.floor(time * sampleRate);
  const duration = Math.floor(sampleRate * 0.055);
  const noise = createNoise(Math.floor(time * 20000) + 31);
  for (let i = 0; i < duration && start + i < data.length; i += 1) {
    const t = i / sampleRate;
    const envelope = Math.exp(-t * 74);
    data[start + i] += noise() * envelope * gain;
  }
}

export function createEndDrumClip(sampleRate = 48000) {
  const bpm = 120;
  const beatSeconds = 60 / bpm;
  const duration = 4;
  const data = new Float32Array(Math.floor(duration * sampleRate));

  for (let beat = 0; beat < 8; beat += 1) {
    const time = beat * beatSeconds;
    addKick(data, sampleRate, time, beat % 2 === 0 ? 0.82 : 0.63);
    addHat(data, sampleRate, time + beatSeconds / 2, 0.11);
    if (beat % 2 === 1) addSnare(data, sampleRate, time, 0.42);
  }

  addSnare(data, sampleRate, 3.86, 0.9);
  addKick(data, sampleRate, 3.91, 0.54);

  return {
    name: "末尾鼓点片段 · 120 BPM",
    description: "120 BPM 鼓组，最后一个军鼓在结束前 140 ms 被硬切。",
    source: "end-drums",
    bpm,
    sampleRate,
    channels: [data]
  };
}
