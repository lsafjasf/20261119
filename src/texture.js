const TAU = Math.PI * 2;

function hash2(x, y, seed) {
  let h = Math.imul(x, 374761393) ^ Math.imul(y, 668265263) ^ Math.imul(seed, 1442695041);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
}

function smooth(value) {
  return value * value * (3 - 2 * value);
}

function makeNoiseLayer(size, cell, seed) {
  const gridSize = Math.ceil(size / cell) + 2;
  const grid = new Float32Array(gridSize * gridSize);
  for (let y = 0; y < gridSize; y += 1) {
    for (let x = 0; x < gridSize; x += 1) {
      grid[y * gridSize + x] = hash2(x, y, seed);
    }
  }

  const output = new Float32Array(size * size);
  for (let y = 0; y < size; y += 1) {
    const gy = y / cell;
    const y0 = Math.floor(gy);
    const fy = smooth(gy - y0);
    for (let x = 0; x < size; x += 1) {
      const gx = x / cell;
      const x0 = Math.floor(gx);
      const fx = smooth(gx - x0);
      const a = grid[y0 * gridSize + x0];
      const b = grid[y0 * gridSize + x0 + 1];
      const c = grid[(y0 + 1) * gridSize + x0];
      const d = grid[(y0 + 1) * gridSize + x0 + 1];
      const top = a + (b - a) * fx;
      const bottom = c + (d - c) * fx;
      output[y * size + x] = top + (bottom - top) * fy;
    }
  }
  return output;
}

export const INTERFERENCE_COMPONENTS = [
  {
    fx: 0.01,
    fy: 0.024,
    amplitude: 38,
    phase: 0.9,
    color: [1, 0.12, 0.35],
    label: "低频摩尔纹峰对",
  },
  {
    fx: 0.047,
    fy: 0.021,
    amplitude: 30,
    phase: 0.4,
    color: [0.9, 0.08, 0.46],
    label: "主干扰峰对",
  },
  {
    fx: -0.034,
    fy: 0.067,
    amplitude: 19,
    phase: 1.8,
    color: [0.18, 0.34, 0.82],
    label: "交织干扰峰对",
  },
  {
    fx: 0.144,
    fy: -0.024,
    amplitude: 9,
    phase: 2.5,
    color: [0.64, 0.1, 0.26],
    label: "色差谐波峰对",
  },
];

export const TEXTURE_COMPONENTS = [
  { fx: 1 / 9.6, fy: 0, amplitude: 8, phase: 0.2, label: "织物纬向周期" },
  { fx: 0, fy: 1 / 9.6, amplitude: 8, phase: 1.1, label: "织物经向周期" },
];

function clampByte(value) {
  return value < 0 ? 0 : value > 255 ? 255 : Math.round(value);
}

export function generateTestSample(seed = 42, size = 512) {
  const layer64 = makeNoiseLayer(size, 64, seed + 1);
  const layer28 = makeNoiseLayer(size, 28, seed + 2);
  const layer11 = makeNoiseLayer(size, 11, seed + 3);
  const layer5 = makeNoiseLayer(size, 5, seed + 4);
  const base = new Uint8ClampedArray(size * size * 4);
  const source = new Uint8ClampedArray(size * size * 4);
  const interference = new Float32Array(size * size);
  const half = size / 2;
  const centerX = half * 0.94;
  const centerY = half * 0.98;

  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const index = y * size + x;
      const noise =
        layer64[index] * 0.5 +
        layer28[index] * 0.27 +
        layer11[index] * 0.15 +
        layer5[index] * 0.08;
      const fiber = Math.sin((x * 0.54 + y * 0.18) * 0.72 + layer28[index] * 5.4);
      const weaveX = Math.cos((TAU * x) / 9.6 + 0.2);
      const weaveY = Math.cos((TAU * y) / 9.6 + 1.1);
      const dx = (x - centerX) / half;
      const dy = (y - centerY) / half;
      const vignette = 1 - 0.17 * Math.min(1.45, dx * dx + dy * dy);
      const shade = (0.63 + (noise - 0.5) * 0.7 + fiber * 0.036 + (weaveX + weaveY) * 0.036) * vignette;
      const grain = (hash2(x, y, seed + 8) - 0.5) * 7;
      const pixel = index * 4;

      base[pixel] = clampByte(128 * shade + 34 + grain);
      base[pixel + 1] = clampByte(123 * shade + 33 + grain * 0.8);
      base[pixel + 2] = clampByte(96 * shade + 30 + grain * 0.55);
      base[pixel + 3] = 255;

      let signal = 0;
      let redShift = 0;
      let blueShift = 0;
      for (let c = 0; c < INTERFERENCE_COMPONENTS.length; c += 1) {
        const component = INTERFERENCE_COMPONENTS[c];
        const wave = Math.sin(TAU * (component.fx * x + component.fy * y) + component.phase);
        const value = wave * component.amplitude;
        signal += value;
        redShift += value * component.color[0];
        blueShift += value * component.color[2];
      }
      interference[index] = signal;
      source[pixel] = clampByte(base[pixel] + redShift * 0.82);
      source[pixel + 1] = clampByte(base[pixel + 1] + signal * 0.2);
      source[pixel + 2] = clampByte(base[pixel + 2] + blueShift * 0.78);
      source[pixel + 3] = 255;
    }
  }

  return {
    width: size,
    height: size,
    seed,
    base,
    source,
    interference,
  };
}

export function sampleMetadata() {
  return {
    interference: INTERFERENCE_COMPONENTS.map((component) => ({
      ...component,
      label: component.label,
    })),
    texture: TEXTURE_COMPONENTS.map((component) => ({ ...component })),
  };
}
