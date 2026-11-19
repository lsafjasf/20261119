const LAYOUTS = {
  444: {
    label: "4:4:4",
    short: "FULL",
    hSub: 1,
    vSub: 1,
    description: "每个亮度像素各有一个 Cb / Cr 样本，色度平面与亮度平面同尺寸。",
    position: "4:4:4 样本位于每个亮度像素中心，Cb / Cr 与 Y′ 精确共址。",
    odd: "宽高均为奇数时没有额外边界样本；每个像素仍对应一个色度样本。",
  },
  422: {
    label: "4:2:2",
    short: "HORIZONTAL ÷2",
    hSub: 2,
    vSub: 1,
    description: "每 2 × 1 个亮度像素共享一个色度样本，样本位于像素块水平方向的几何中心。",
    position: "4:2:2 样本位于每 2 × 1 亮度块的质心，垂直方向与亮度像素逐行共址。",
    odd: "宽度为奇数时，末列只有 1 个有效像素；末样本落在末列中心，而不是虚构的第二列。",
  },
  420: {
    label: "4:2:0",
    short: "H ÷2 · V ÷2",
    hSub: 2,
    vSub: 2,
    description: "每 2 × 2 个亮度像素共享一个色度样本，样本位于像素块的几何中心。",
    position: "4:2:0 样本位于每 2 × 2 亮度块的质心，即四个亮度像素中心的平均位置。",
    odd: "宽或高为奇数时，末列/末行只平均实际存在的像素；末样本位置随覆盖范围收缩。",
  },
};

const METHODS = {
  nearest: {
    label: "最近邻",
    description: "最近邻：每个亮度像素选取欧氏距离最近的色度样本，色块边界保持硬切。",
  },
  bilinear: {
    label: "双线性",
    description: "双线性：按真实样本间距做可分离线性插值；越界时复制最外侧样本。",
  },
};

const LAYOUT_ORDER = ["444", "422", "420"];
const METHOD_ORDER = ["nearest", "bilinear"];

const PATTERN_INFO = {
  mixed: {
    name: "复合锐边图",
    description: "彩条 + 单像素竖线/横线，覆盖大面积与高频彩边。",
  },
  bars: {
    name: "饱和彩条",
    description: "七段硬切饱和彩条，用于观察整列色度被平均后的串色范围。",
  },
  lines: {
    name: "1 px 彩线",
    description: "暗底上的单像素竖线与横线，用于放大色度带宽不足造成的高频损失。",
  },
};

const BAR_COLORS = [
  [225, 50, 52],
  [244, 205, 56],
  [35, 194, 100],
  [38, 210, 206],
  [45, 105, 239],
  [218, 70, 190],
  [235, 239, 232],
];

const LINE_COLORS = [
  [255, 80, 61],
  [255, 211, 56],
  [36, 214, 122],
  [90, 224, 208],
  [70, 143, 255],
  [236, 86, 216],
  [247, 248, 238],
];

const state = {
  width: 193,
  height: 129,
  pattern: "mixed",
  layout: "420",
  method: "bilinear",
  seed: 7,
  source: null,
  sourceLab: null,
  ycbcr: null,
  layouts: {},
  results: {},
  maskVisible: true,
  hot: null,
};

const elements = {};

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function mix(a, b, t) {
  return a + (b - a) * t;
}

function seededRandom(seed) {
  let value = seed >>> 0;
  return () => {
    value += 0x6d2b79f5;
    let t = value;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function setPixel(rgb, width, x, y, color) {
  const index = (y * width + x) * 3;
  rgb[index] = color[0];
  rgb[index + 1] = color[1];
  rgb[index + 2] = color[2];
}

function fillRect(rgb, width, height, x0, y0, x1, y1, color) {
  const left = clamp(Math.floor(x0), 0, width);
  const top = clamp(Math.floor(y0), 0, height);
  const right = clamp(Math.ceil(x1), 0, width);
  const bottom = clamp(Math.ceil(y1), 0, height);
  for (let y = top; y < bottom; y += 1) {
    for (let x = left; x < right; x += 1) {
      setPixel(rgb, width, x, y, color);
    }
  }
}

function generateColorBars(rgb, width, height, y0 = 0, y1 = height) {
  for (let y = y0; y < y1; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const bar = Math.min(BAR_COLORS.length - 1, Math.floor((x * BAR_COLORS.length) / width));
      setPixel(rgb, width, x, y, BAR_COLORS[bar]);
    }
  }
}

function generateVerticalLines(rgb, width, height, x0, x1, y0, y1, random) {
  const background = [12, 17, 19];
  fillRect(rgb, width, height, x0, y0, x1, y1, background);
  const start = x0 + 3 + Math.floor(random() * 3);
  const span = width >= 180 ? 7 : 6;
  let lineIndex = 0;
  for (let x = start; x < x1; x += span) {
    const color = LINE_COLORS[lineIndex % LINE_COLORS.length];
    fillRect(rgb, width, height, x, y0, x + 1, y1, color);
    lineIndex += 1;
  }
}

function generateHorizontalLines(rgb, width, height, x0, x1, y0, y1, random) {
  const background = [14, 18, 20];
  fillRect(rgb, width, height, x0, y0, x1, y1, background);
  const start = y0 + 3 + Math.floor(random() * 3);
  const span = height >= 120 ? 8 : 7;
  let lineIndex = 0;
  for (let y = start; y < y1; y += span) {
    const color = LINE_COLORS[(lineIndex + 3) % LINE_COLORS.length];
    fillRect(rgb, width, height, x0, y, x1, y + 1, color);
    lineIndex += 1;
  }
}

function generatePattern(width, height, pattern, seed) {
  const rgb = new Float32Array(width * height * 3);
  const random = seededRandom(seed);

  if (pattern === "bars") {
    generateColorBars(rgb, width, height);
    return rgb;
  }

  if (pattern === "lines") {
    const verticalWidth = Math.round(width * 0.62);
    generateVerticalLines(rgb, width, height, 0, verticalWidth, 0, height, random);
    generateHorizontalLines(rgb, width, height, verticalWidth, width, 0, height, random);
    return rgb;
  }

  const barsBottom = Math.round(height * 0.55);
  const split = Math.round(width * 0.62);
  generateColorBars(rgb, width, height, 0, barsBottom);
  generateVerticalLines(rgb, width, height, 0, split, barsBottom, height, random);
  generateHorizontalLines(rgb, width, height, split, width, barsBottom, height, random);
  return rgb;
}

function buildEdgeMask(rgb, width, height) {
  const mask = new Uint8Array(width * height);
  let maxStep = 0;
  let boundaryCount = 0;

  const compare = (x, y, x2, y2) => {
    const a = (y * width + x) * 3;
    const b = (y2 * width + x2) * 3;
    const dr = Math.abs(rgb[a] - rgb[b]);
    const dg = Math.abs(rgb[a + 1] - rgb[b + 1]);
    const db = Math.abs(rgb[a + 2] - rgb[b + 2]);
    const step = Math.max(dr, dg, db);
    if (step < 30) {
      return;
    }
    mask[y * width + x] = 1;
    mask[y2 * width + x2] = 1;
    boundaryCount += 1;
    maxStep = Math.max(maxStep, step);
  };

  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width - 1; x += 1) {
      compare(x, y, x + 1, y);
    }
  }
  for (let y = 0; y < height - 1; y += 1) {
    for (let x = 0; x < width; x += 1) {
      compare(x, y, x, y + 1);
    }
  }

  let edgePixels = 0;
  for (let i = 0; i < mask.length; i += 1) {
    edgePixels += mask[i];
  }

  return { mask, maxStep, boundaryCount, edgePixels };
}

function rgbToYcbcr(rgb) {
  const length = rgb.length / 3;
  const yPlane = new Float32Array(length);
  const cbPlane = new Float32Array(length);
  const crPlane = new Float32Array(length);

  for (let i = 0; i < length; i += 1) {
    const offset = i * 3;
    const r = rgb[offset];
    const g = rgb[offset + 1];
    const b = rgb[offset + 2];
    yPlane[i] = 0.299 * r + 0.587 * g + 0.114 * b;
    cbPlane[i] = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b;
    crPlane[i] = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b;
  }

  return { yPlane, cbPlane, crPlane };
}

function ycbcrToRgb(yValue, cb, cr) {
  return [
    yValue + 1.402 * (cr - 128),
    yValue - 0.344136 * (cb - 128) - 0.714136 * (cr - 128),
    yValue + 1.772 * (cb - 128),
  ];
}

function encodeChromaLayout(ycbcr, width, height, layoutKey) {
  const layout = LAYOUTS[layoutKey];
  const planeWidth = Math.ceil(width / layout.hSub);
  const planeHeight = Math.ceil(height / layout.vSub);
  const cb = new Float32Array(planeWidth * planeHeight);
  const cr = new Float32Array(planeWidth * planeHeight);
  const positionsX = new Float32Array(planeWidth);
  const positionsY = new Float32Array(planeHeight);
  const cellWidths = new Int16Array(planeWidth);
  const cellHeights = new Int16Array(planeHeight);

  for (let py = 0; py < planeHeight; py += 1) {
    const sourceY0 = py * layout.vSub;
    const sourceY1 = Math.min(sourceY0 + layout.vSub, height);
    positionsY[py] = (sourceY0 + sourceY1 - 1) / 2;
    cellHeights[py] = sourceY1 - sourceY0;
  }

  for (let px = 0; px < planeWidth; px += 1) {
    const sourceX0 = px * layout.hSub;
    const sourceX1 = Math.min(sourceX0 + layout.hSub, width);
    positionsX[px] = (sourceX0 + sourceX1 - 1) / 2;
    cellWidths[px] = sourceX1 - sourceX0;
  }

  for (let py = 0; py < planeHeight; py += 1) {
    const sourceY0 = py * layout.vSub;
    const sourceY1 = Math.min(sourceY0 + layout.vSub, height);
    for (let px = 0; px < planeWidth; px += 1) {
      const sourceX0 = px * layout.hSub;
      const sourceX1 = Math.min(sourceX0 + layout.hSub, width);
      let cbSum = 0;
      let crSum = 0;
      let count = 0;
      for (let y = sourceY0; y < sourceY1; y += 1) {
        for (let x = sourceX0; x < sourceX1; x += 1) {
          const index = y * width + x;
          cbSum += ycbcr.cbPlane[index];
          crSum += ycbcr.crPlane[index];
          count += 1;
        }
      }
      const planeIndex = py * planeWidth + px;
      cb[planeIndex] = cbSum / count;
      cr[planeIndex] = crSum / count;
    }
  }

  return {
    cb,
    cr,
    width: planeWidth,
    height: planeHeight,
    positionsX,
    positionsY,
    cellWidths,
    cellHeights,
  };
}

function nearestIndex(positions, value) {
  let low = 0;
  let high = positions.length - 1;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (positions[middle] < value) {
      low = middle + 1;
    } else {
      high = middle;
    }
  }
  if (low === 0) {
    return 0;
  }
  const left = low - 1;
  return Math.abs(value - positions[left]) <= Math.abs(positions[low] - value) ? left : low;
}

function linearBracket(positions, value) {
  if (value <= positions[0]) {
    return [0, 0, 0];
  }
  const last = positions.length - 1;
  if (value >= positions[last]) {
    return [last, last, 0];
  }
  let low = 0;
  let high = last;
  while (low + 1 < high) {
    const middle = (low + high) >> 1;
    if (positions[middle] <= value) {
      low = middle;
    } else {
      high = middle;
    }
  }
  const span = positions[high] - positions[low];
  return [low, high, span === 0 ? 0 : (value - positions[low]) / span];
}

function reconstruct(ycbcr, width, height, layoutKey, method, encodedChroma = null) {
  const chroma = encodedChroma || encodeChromaLayout(ycbcr, width, height, layoutKey);
  const rgb = new Float32Array(width * height * 3);
  const cbPixels = new Float32Array(width * height);
  const crPixels = new Float32Array(width * height);
  const planeWidth = chroma.width;
  const planeHeight = chroma.height;

  for (let y = 0; y < height; y += 1) {
    const yBracket = linearBracket(chroma.positionsY, y);
    const nearestY = nearestIndex(chroma.positionsY, y);
    for (let x = 0; x < width; x += 1) {
      const nearestX = nearestIndex(chroma.positionsX, x);
      let cb;
      let cr;

      if (method === "nearest") {
        const index = nearestY * planeWidth + nearestX;
        cb = chroma.cb[index];
        cr = chroma.cr[index];
      } else {
        const [x0, x1, tx] = linearBracket(chroma.positionsX, x);
        const [y0, y1, ty] = yBracket;
        const cb00 = chroma.cb[y0 * planeWidth + x0];
        const cb10 = chroma.cb[y0 * planeWidth + x1];
        const cb01 = chroma.cb[y1 * planeWidth + x0];
        const cb11 = chroma.cb[y1 * planeWidth + x1];
        const cr00 = chroma.cr[y0 * planeWidth + x0];
        const cr10 = chroma.cr[y0 * planeWidth + x1];
        const cr01 = chroma.cr[y1 * planeWidth + x0];
        const cr11 = chroma.cr[y1 * planeWidth + x1];
        cb = mix(mix(cb00, cb10, tx), mix(cb01, cb11, tx), ty);
        cr = mix(mix(cr00, cr10, tx), mix(cr01, cr11, tx), ty);
      }

      const pixelIndex = y * width + x;
      const yValue = ycbcr.yPlane[pixelIndex];
      cbPixels[pixelIndex] = cb;
      crPixels[pixelIndex] = cr;
      const output = ycbcrToRgb(yValue, cb, cr);
      const offset = pixelIndex * 3;
      rgb[offset] = clamp(output[0], 0, 255);
      rgb[offset + 1] = clamp(output[1], 0, 255);
      rgb[offset + 2] = clamp(output[2], 0, 255);
    }
  }

  return { rgb, cbPixels, crPixels, chroma };
}

function srgbToLinear(value) {
  const normalized = value / 255;
  return normalized <= 0.04045
    ? normalized / 12.92
    : ((normalized + 0.055) / 1.055) ** 2.4;
}

function rgbToLabArray(rgb) {
  const count = rgb.length / 3;
  const labs = new Float32Array(count * 3);

  for (let i = 0; i < count; i += 1) {
    const offset = i * 3;
    const r = srgbToLinear(rgb[offset]);
    const g = srgbToLinear(rgb[offset + 1]);
    const b = srgbToLinear(rgb[offset + 2]);
    const x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047;
    const y = 0.2126729 * r + 0.7151522 * g + 0.072175 * b;
    const z = (0.0193339 * r + 0.119192 * g + 0.9503041 * b) / 1.08883;

    const fx = x > 0.0088564517 ? Math.cbrt(x) : 7.787037 * x + 16 / 116;
    const fy = y > 0.0088564517 ? Math.cbrt(y) : 7.787037 * y + 16 / 116;
    const fz = z > 0.0088564517 ? Math.cbrt(z) : 7.787037 * z + 16 / 116;

    labs[offset] = 116 * fy - 16;
    labs[offset + 1] = 500 * (fx - fy);
    labs[offset + 2] = 200 * (fy - fz);
  }

  return labs;
}

function deltaE2000(lab1, lab2) {
  const l1 = lab1[0];
  const a1 = lab1[1];
  const b1 = lab1[2];
  const l2 = lab2[0];
  const a2 = lab2[1];
  const b2 = lab2[2];

  const c1 = Math.hypot(a1, b1);
  const c2 = Math.hypot(a2, b2);
  const cBar = (c1 + c2) / 2;
  const cBar7 = cBar ** 7;
  const g = 0.5 * (1 - Math.sqrt(cBar7 / (cBar7 + 25 ** 7)));
  const a1p = (1 + g) * a1;
  const a2p = (1 + g) * a2;
  const c1p = Math.hypot(a1p, b1);
  const c2p = Math.hypot(a2p, b2);

  let h1p = Math.atan2(b1, a1p) * 180 / Math.PI;
  let h2p = Math.atan2(b2, a2p) * 180 / Math.PI;
  if (h1p < 0) h1p += 360;
  if (h2p < 0) h2p += 360;
  if (c1p === 0) h1p = 0;
  if (c2p === 0) h2p = 0;

  const deltaLp = l2 - l1;
  const deltaCp = c2p - c1p;
  let deltaHp = 0;
  if (c1p * c2p !== 0) {
    const hDiff = h2p - h1p;
    if (hDiff > 180) {
      deltaHp = hDiff - 360;
    } else if (hDiff < -180) {
      deltaHp = hDiff + 360;
    } else {
      deltaHp = hDiff;
    }
    deltaHp = 2 * Math.sqrt(c1p * c2p) * Math.sin((deltaHp / 2) * Math.PI / 180);
  }

  const lBar = (l1 + l2) / 2;
  const cBarp = (c1p + c2p) / 2;
  let hBarp = h1p + h2p;
  if (c1p * c2p !== 0) {
    const hSum = h1p + h2p;
    const hAbsDiff = Math.abs(h1p - h2p);
    if (hAbsDiff <= 180) {
      hBarp = hSum / 2;
    } else if (hSum < 360) {
      hBarp = (hSum + 360) / 2;
    } else {
      hBarp = (hSum - 360) / 2;
    }
  }

  const hRad = hBarp * Math.PI / 180;
  const t = 1
    - 0.17 * Math.cos(hRad - 30 * Math.PI / 180)
    + 0.24 * Math.cos(2 * hRad)
    + 0.32 * Math.cos(3 * hRad + 6 * Math.PI / 180)
    - 0.2 * Math.cos(4 * hRad - 63 * Math.PI / 180);
  const deltaTheta = 30 * Math.exp(-(((hBarp - 275) / 25) ** 2));
  const cBarp7 = cBarp ** 7;
  const rc = 2 * Math.sqrt(cBarp7 / (cBarp7 + 25 ** 7));
  const lBarOffset = (lBar - 50) ** 2;
  const sl = 1 + (0.015 * lBarOffset) / Math.sqrt(20 + lBarOffset);
  const sc = 1 + 0.045 * cBarp;
  const sh = 1 + 0.015 * cBarp * t;
  const rt = -Math.sin(2 * deltaTheta * Math.PI / 180) * rc;

  const lTerm = deltaLp / sl;
  const cTerm = deltaCp / sc;
  const hTerm = deltaHp / sh;
  return Math.sqrt(Math.max(0, lTerm ** 2 + cTerm ** 2 + hTerm ** 2 + rt * cTerm * hTerm));
}

function percentile(sortedValues, ratio) {
  if (sortedValues.length === 0) {
    return 0;
  }
  const index = clamp(Math.floor((sortedValues.length - 1) * ratio), 0, sortedValues.length - 1);
  return sortedValues[index];
}

function measureResult(sourceRgb, sourceLab, resultRgb, mask) {
  const count = mask.length;
  const resultLab = rgbToLabArray(resultRgb);
  const deltaE = new Float32Array(count);
  const rgbError = new Float32Array(count);
  const edgeValues = [];
  let deltaSum = 0;
  let rgbSum = 0;
  let maxIndex = 0;
  let maxDelta = -1;

  const sourceLabPixel = [0, 0, 0];
  const resultLabPixel = [0, 0, 0];

  for (let i = 0; i < count; i += 1) {
    const offset = i * 3;
    const dr = Math.abs(resultRgb[offset] - sourceRgb[offset]);
    const dg = Math.abs(resultRgb[offset + 1] - sourceRgb[offset + 1]);
    const db = Math.abs(resultRgb[offset + 2] - sourceRgb[offset + 2]);
    rgbError[i] = (dr + dg + db) / 3;

    sourceLabPixel[0] = sourceLab[offset];
    sourceLabPixel[1] = sourceLab[offset + 1];
    sourceLabPixel[2] = sourceLab[offset + 2];
    resultLabPixel[0] = resultLab[offset];
    resultLabPixel[1] = resultLab[offset + 1];
    resultLabPixel[2] = resultLab[offset + 2];
    const difference = deltaE2000(sourceLabPixel, resultLabPixel);
    deltaE[i] = difference;

    if (mask[i] && difference > maxDelta) {
      maxDelta = difference;
      maxIndex = i;
    }
    if (mask[i]) {
      deltaSum += difference;
      rgbSum += rgbError[i];
      edgeValues.push(difference);
    }
  }

  edgeValues.sort((a, b) => a - b);
  const edgeCount = edgeValues.length || 1;
  return {
    deltaE,
    rgbError,
    edgeMeanDeltaE: deltaSum / edgeCount,
    edgeRgbMae: rgbSum / edgeCount,
    edgeP95DeltaE: percentile(edgeValues, 0.95),
    maxDeltaE: maxDelta,
    hotIndex: maxIndex,
  };
}

function drawRgbCanvas(canvas, rgb, width, height) {
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  const image = context.createImageData(width, height);
  for (let i = 0; i < width * height; i += 1) {
    const offset = i * 3;
    image.data[i * 4] = Math.round(rgb[offset]);
    image.data[i * 4 + 1] = Math.round(rgb[offset + 1]);
    image.data[i * 4 + 2] = Math.round(rgb[offset + 2]);
    image.data[i * 4 + 3] = 255;
  }
  context.putImageData(image, 0, 0);
}

function drawEdgeMask(canvas, mask, width, height) {
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  const image = context.createImageData(width, height);
  for (let i = 0; i < width * height; i += 1) {
    if (mask[i]) {
      image.data[i * 4] = 235;
      image.data[i * 4 + 1] = 241;
      image.data[i * 4 + 2] = 236;
      image.data[i * 4 + 3] = 112;
    }
  }
  context.putImageData(image, 0, 0);
}

function colorStops(stops, t) {
  const scaled = clamp(t, 0, 1) * (stops.length - 1);
  const lower = Math.floor(scaled);
  const upper = Math.min(stops.length - 1, lower + 1);
  const amount = scaled - lower;
  return [
    Math.round(mix(stops[lower][0], stops[upper][0], amount)),
    Math.round(mix(stops[lower][1], stops[upper][1], amount)),
    Math.round(mix(stops[lower][2], stops[upper][2], amount)),
  ];
}

const ERROR_STOPS = [
  [5, 9, 11],
  [18, 43, 56],
  [127, 49, 86],
  [255, 105, 81],
  [255, 238, 183],
];

function errorColor(value) {
  return colorStops(ERROR_STOPS, value / 20);
}

function drawErrorCanvas(canvas, errors, width, height) {
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  const image = context.createImageData(width, height);
  for (let i = 0; i < width * height; i += 1) {
    const color = errorColor(errors[i]);
    image.data[i * 4] = color[0];
    image.data[i * 4 + 1] = color[1];
    image.data[i * 4 + 2] = color[2];
    image.data[i * 4 + 3] = 255;
  }
  context.putImageData(image, 0, 0);
}

const CB_STOPS = [
  [183, 137, 54],
  [64, 72, 68],
  [67, 151, 222],
];

const CR_STOPS = [
  [56, 146, 109],
  [65, 69, 66],
  [217, 86, 118],
];

function drawChromaPlane(canvas, plane, width, height, type) {
  const scale = width < 120 ? 4 : 3;
  canvas.width = width * scale;
  canvas.height = height * scale;
  const context = canvas.getContext("2d");
  const stops = type === "cb" ? CB_STOPS : CR_STOPS;

  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const value = plane[y * width + x];
      const t = (value - 64) / 128;
      const color = colorStops(stops, t);
      context.fillStyle = `rgb(${color[0]}, ${color[1]}, ${color[2]})`;
      context.fillRect(x * scale, y * scale, scale, scale);
    }
  }

  context.strokeStyle = "rgba(3, 7, 8, 0.24)";
  context.lineWidth = 1;
  for (let x = 0; x < width; x += 1) {
    context.beginPath();
    context.moveTo(x * scale + 0.5, 0);
    context.lineTo(x * scale + 0.5, height * scale);
    context.stroke();
  }
  for (let y = 0; y < height; y += 1) {
    context.beginPath();
    context.moveTo(0, y * scale + 0.5);
    context.lineTo(width * scale, y * scale + 0.5);
    context.stroke();
  }
}

function drawLoupe(canvas, result, mode, cropSize = 24, scale = 8) {
  if (!result) {
    return;
  }
  const { width, height } = state;
  const hotX = state.hot.x;
  const hotY = state.hot.y;
  const x0 = clamp(hotX - Math.floor(cropSize / 2), 0, Math.max(0, width - cropSize));
  const y0 = clamp(hotY - Math.floor(cropSize / 2), 0, Math.max(0, height - cropSize));
  canvas.width = cropSize * scale;
  canvas.height = cropSize * scale;
  const context = canvas.getContext("2d");
  const source = state.source.rgb;
  const reconstructed = result.rgb;

  for (let y = 0; y < cropSize; y += 1) {
    for (let x = 0; x < cropSize; x += 1) {
      const sourceX = Math.min(width - 1, x0 + x);
      const sourceY = Math.min(height - 1, y0 + y);
      const index = sourceY * width + sourceX;
      const offset = index * 3;
      let color;
      if (mode === "source") {
        color = [source[offset], source[offset + 1], source[offset + 2]];
      } else if (mode === "reconstruction") {
        color = [reconstructed[offset], reconstructed[offset + 1], reconstructed[offset + 2]];
      } else {
        color = errorColor(result.metrics.deltaE[index]);
      }
      context.fillStyle = `rgb(${Math.round(color[0])}, ${Math.round(color[1])}, ${Math.round(color[2])})`;
      context.fillRect(x * scale, y * scale, scale, scale);
    }
  }

  const markerX = (hotX - x0) * scale;
  const markerY = (hotY - y0) * scale;
  context.strokeStyle = "rgba(255, 255, 255, 0.92)";
  context.lineWidth = 2;
  context.beginPath();
  context.moveTo(markerX - 10, markerY - 10);
  context.lineTo(markerX - 3, markerY - 3);
  context.moveTo(markerX + 10, markerY - 10);
  context.lineTo(markerX + 3, markerY - 3);
  context.moveTo(markerX - 10, markerY + 10);
  context.lineTo(markerX - 3, markerY + 3);
  context.moveTo(markerX + 10, markerY + 10);
  context.lineTo(markerX + 3, markerY + 3);
  context.stroke();
  context.strokeStyle = "rgba(8, 11, 13, 0.8)";
  context.lineWidth = 1;
  context.strokeRect(markerX - scale / 2 + 0.5, markerY - scale / 2 + 0.5, scale - 1, scale - 1);
}

function formatInteger(value) {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function formatMetric(value) {
  if (!Number.isFinite(value)) {
    return "—";
  }
  return value < 0.01 ? "<0.01" : value.toFixed(2);
}

function buildExperiment() {
  state.source = {
    rgb: generatePattern(state.width, state.height, state.pattern, state.seed),
  };
  Object.assign(state.source, buildEdgeMask(state.source.rgb, state.width, state.height));
  state.sourceLab = rgbToLabArray(state.source.rgb);
  state.ycbcr = rgbToYcbcr(state.source.rgb);
  state.layouts = {};
  state.results = {};

  for (const layoutKey of LAYOUT_ORDER) {
    const chroma = encodeChromaLayout(
      state.ycbcr,
      state.width,
      state.height,
      layoutKey,
    );
    state.layouts[layoutKey] = chroma;

    for (const method of METHOD_ORDER) {
      const reconstructed = reconstruct(
        state.ycbcr,
        state.width,
        state.height,
        layoutKey,
        method,
        chroma,
      );
      reconstructed.metrics = measureResult(
        state.source.rgb,
        state.sourceLab,
        reconstructed.rgb,
        state.source.mask,
      );
      state.results[`${layoutKey}-${method}`] = reconstructed;
    }
  }
}

function currentResult() {
  return state.results[`${state.layout}-${state.method}`];
}

function setHotPixelFromResult(result) {
  const hotIndex = result.metrics.hotIndex;
  state.hot = {
    x: hotIndex % state.width,
    y: Math.floor(hotIndex / state.width),
    index: hotIndex,
  };
}

function renderSource() {
  drawRgbCanvas(elements.sourceCanvas, state.source.rgb, state.width, state.height);
  drawEdgeMask(elements.edgeCanvas, state.source.mask, state.width, state.height);
  elements.edgeCanvas.classList.toggle("is-hidden", !state.maskVisible);
  document.documentElement.style.setProperty("--source-aspect", `${state.width} / ${state.height}`);
  elements.sourceDimensions.textContent = `${state.width} × ${state.height} px`;
  elements.patternReadout.textContent = PATTERN_INFO[state.pattern].name;
  elements.patternDescription.textContent = PATTERN_INFO[state.pattern].description;
  elements.edgePixelCount.textContent = formatInteger(state.source.edgePixels);
  elements.edgeCoverage.textContent =
    `${((state.source.edgePixels / (state.width * state.height)) * 100).toFixed(1)}%`;
  elements.maxStep.textContent = `${Math.round(state.source.maxStep)} / 255`;
  elements.oddReadout.textContent =
    `${state.width % 2 ? "ODD" : "EVEN"} / ${state.height % 2 ? "ODD" : "EVEN"}`;
  elements.footerSize.textContent = `${state.width} × ${state.height} / ${state.width % 2 ? "ODD" : "EVEN"} × ${state.height % 2 ? "ODD" : "EVEN"}`;
}

function matrixCellMarkup(layoutKey, method, result) {
  const layout = LAYOUTS[layoutKey];
  const chroma = state.layouts[layoutKey];
  const selected = layoutKey === state.layout && method === state.method;
  return `
    <button
      class="matrix-cell${selected ? " is-selected" : ""}"
      type="button"
      data-layout="${layoutKey}"
      data-method="${method}"
      data-mobile-label="${layout.label} · ${METHODS[method].label}"
      aria-label="${layout.label} ${METHODS[method].label}，彩边平均 ΔE00 ${formatMetric(result.metrics.edgeMeanDeltaE)}"
    >
      <div class="matrix-canvas-wrap" style="--matrix-aspect: ${state.width} / ${state.height}">
        <canvas data-matrix-canvas="${layoutKey}-${method}"></canvas>
      </div>
      <div class="matrix-cell-footer">
        <span>${chroma.width} × ${chroma.height} px</span>
        <strong>${formatMetric(result.metrics.edgeMeanDeltaE)} <i>ΔE00</i></strong>
      </div>
    </button>
  `;
}

function renderMatrix() {
  elements.matrixNearest.innerHTML = LAYOUT_ORDER
    .map((layoutKey) => matrixCellMarkup(layoutKey, "nearest", state.results[`${layoutKey}-nearest`]))
    .join("");
  elements.matrixBilinear.innerHTML = LAYOUT_ORDER
    .map((layoutKey) => matrixCellMarkup(layoutKey, "bilinear", state.results[`${layoutKey}-bilinear`]))
    .join("");

  document.querySelectorAll("[data-matrix-canvas]").forEach((canvas) => {
    const [layoutKey, method] = canvas.getAttribute("data-matrix-canvas").split("-");
    const result = state.results[`${layoutKey}-${method}`];
    drawRgbCanvas(canvas, result.rgb, state.width, state.height);
  });

  document.querySelectorAll(".matrix-column-head").forEach((heading) => {
    heading.classList.toggle(
      "is-selected",
      heading.getAttribute("data-layout-head") === state.layout,
    );
  });
}

function renderMiniDiagram(layoutKey) {
  const layout = LAYOUTS[layoutKey];
  const columns = 8;
  const rows = 4;
  elements.sampleDiagram.innerHTML = "";
  elements.sampleDiagram.style.setProperty("--grid-w", columns);
  elements.sampleDiagram.style.setProperty("--grid-h", rows);

  for (let y = 0; y < rows; y += 1) {
    for (let x = 0; x < columns; x += 1) {
      const pixel = document.createElement("span");
      pixel.className = "mini-pixel";
      pixel.style.gridColumn = String(x + 1);
      pixel.style.gridRow = String(y + 1);
      elements.sampleDiagram.appendChild(pixel);
    }
  }

  const chromaWidth = Math.ceil(columns / layout.hSub);
  const chromaHeight = Math.ceil(rows / layout.vSub);
  for (let py = 0; py < chromaHeight; py += 1) {
    const y0 = py * layout.vSub;
    const y1 = Math.min(y0 + layout.vSub, rows);
    const sampleY = (y0 + y1 - 1) / 2;
    for (let px = 0; px < chromaWidth; px += 1) {
      const x0 = px * layout.hSub;
      const x1 = Math.min(x0 + layout.hSub, columns);
      const sampleX = (x0 + x1 - 1) / 2;
      const dot = document.createElement("span");
      dot.className = "mini-dot";
      dot.style.left = `${((sampleX + 0.5) / columns) * 100}%`;
      dot.style.top = `${((sampleY + 0.5) / rows) * 100}%`;
      elements.sampleDiagram.appendChild(dot);
    }
  }
}

function renderInspector() {
  const layout = LAYOUTS[state.layout];
  const chroma = state.layouts[state.layout];
  const result = currentResult();
  elements.inspectorLayout.textContent = layout.label;
  elements.inspectorMethodPill.textContent = `${METHODS[state.method].label}重建`;
  elements.cbSize.textContent = `${chroma.width} × ${chroma.height} px`;
  elements.crSize.textContent = `${chroma.width} × ${chroma.height} px`;
  elements.samplingDescription.textContent = layout.description;
  elements.samplePositionRule.textContent = layout.position;
  elements.oddRule.textContent = layout.odd;
  elements.methodDescription.textContent = METHODS[state.method].description;
  elements.planeSampleCount.textContent = formatInteger(chroma.width * chroma.height);
  const ratioText = `${(((chroma.width * chroma.height) / (state.width * state.height)) * 100).toFixed(1)}%`;
  elements.chromaRatio.textContent = ratioText;
  elements.readoutChromaRatio.textContent = ratioText;
  elements.readoutChromaBar.style.width = ratioText;
  elements.inspectorDeltaE.textContent = formatMetric(result.metrics.edgeMeanDeltaE);
  drawChromaPlane(elements.cbCanvas, chroma.cb, chroma.width, chroma.height, "cb");
  drawChromaPlane(elements.crCanvas, chroma.cr, chroma.width, chroma.height, "cr");
  renderMiniDiagram(state.layout);
}

function renderTable() {
  const rows = [];
  for (const layoutKey of LAYOUT_ORDER) {
    const chroma = state.layouts[layoutKey];
    for (const method of METHOD_ORDER) {
      const result = state.results[`${layoutKey}-${method}`];
      const active = layoutKey === state.layout && method === state.method;
      rows.push(`
        <tr class="${active ? "is-active" : ""}" data-row="${layoutKey}-${method}">
          <td><span class="layout-mark">${LAYOUTS[layoutKey].label}</span></td>
          <td>${chroma.width} × ${chroma.height}</td>
          <td>${chroma.width} × ${chroma.height}</td>
          <td>${METHODS[method].label}</td>
          <td class="${result.metrics.edgeMeanDeltaE < 1 ? "good" : result.metrics.edgeMeanDeltaE > 5 ? "warn" : ""}">
            ${formatMetric(result.metrics.edgeMeanDeltaE)}
          </td>
          <td>${formatMetric(result.metrics.edgeRgbMae)} / 255</td>
          <td>${formatMetric(result.metrics.edgeP95DeltaE)}</td>
          <td>${formatMetric(result.metrics.maxDeltaE)}</td>
        </tr>
      `);
    }
  }
  elements.resultsBody.innerHTML = rows.join("");
}

function renderLoupe() {
  const result = currentResult();
  setHotPixelFromResult(result);
  drawLoupe(elements.loupeSource, result, "source");
  drawLoupe(elements.loupeReconstruction, result, "reconstruction");
  drawLoupe(elements.loupeError, result, "error");
  elements.loupeMethodLabel.textContent = METHODS[state.method].label;
  elements.hotX.textContent = String(state.hot.x).padStart(3, "0");
  elements.hotY.textContent = String(state.hot.y).padStart(3, "0");
  elements.hotDeltaE.textContent = formatMetric(result.metrics.deltaE[state.hot.index]);
  elements.hotRgbError.textContent = formatMetric(result.metrics.rgbError[state.hot.index]);
  elements.hotChroma.textContent =
    `${result.cbPixels[state.hot.index].toFixed(1)} / ${result.crPixels[state.hot.index].toFixed(1)}`;
}

function syncActiveControls() {
  document.querySelectorAll("#pattern-tabs button").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.pattern === state.pattern);
  });
  document.querySelectorAll("#layout-tabs button").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.layout === state.layout);
  });
  document.querySelectorAll("#method-tabs button").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.method === state.method);
  });
}

function renderAll() {
  syncActiveControls();
  renderSource();
  renderMatrix();
  renderInspector();
  renderTable();
  renderLoupe();
}

function refreshSelection() {
  syncActiveControls();
  renderMatrix();
  renderInspector();
  renderTable();
  renderLoupe();
}

function normalizeOddDimension(input, min, max) {
  let value = Number.parseInt(input.value, 10);
  if (!Number.isFinite(value)) {
    value = min;
  }
  value = clamp(value, min, max);
  if (value % 2 === 0) {
    value = value === max ? max - 1 : value + 1;
  }
  input.value = String(value);
  return value;
}

function cacheElements() {
  const ids = [
    "source-canvas",
    "edge-canvas",
    "source-dimensions",
    "pattern-readout",
    "pattern-description",
    "edge-pixel-count",
    "edge-coverage",
    "max-step",
    "odd-readout",
    "matrix-nearest",
    "matrix-bilinear",
    "inspector-layout",
    "inspector-method-pill",
    "cb-size",
    "cr-size",
    "cb-canvas",
    "cr-canvas",
    "sample-diagram",
    "sampling-description",
    "sample-position-rule",
    "odd-rule",
    "method-description",
    "plane-sample-count",
    "chroma-ratio",
    "readout-chroma-ratio",
    "readout-chroma-bar",
    "inspector-delta-e",
    "loupe-source",
    "loupe-reconstruction",
    "loupe-error",
    "loupe-method-label",
    "hot-x",
    "hot-y",
    "hot-delta-e",
    "hot-rgb-error",
    "hot-chroma",
    "results-body",
    "footer-size",
    "width-input",
    "height-input",
    "mask-toggle",
    "regenerate-button",
  ];
  ids.forEach((id) => {
    const key = id.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    elements[key] = document.getElementById(id);
  });
}

function bindEvents() {
  document.getElementById("pattern-tabs").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-pattern]");
    if (!button || button.dataset.pattern === state.pattern) {
      return;
    }
    state.pattern = button.dataset.pattern;
    buildExperiment();
    renderAll();
  });

  document.getElementById("layout-tabs").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-layout]");
    if (!button || button.dataset.layout === state.layout) {
      return;
    }
    state.layout = button.dataset.layout;
    refreshSelection();
  });

  document.getElementById("method-tabs").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-method]");
    if (!button || button.dataset.method === state.method) {
      return;
    }
    state.method = button.dataset.method;
    refreshSelection();
  });

  elements.comparisonMatrix = document.getElementById("comparison-matrix");
  elements.comparisonMatrix.addEventListener("click", (event) => {
    const cell = event.target.closest(".matrix-cell");
    if (!cell) {
      return;
    }
    state.layout = cell.dataset.layout;
    state.method = cell.dataset.method;
    refreshSelection();
  });

  elements.resultsBody.addEventListener("click", (event) => {
    const row = event.target.closest("tr[data-row]");
    if (!row) {
      return;
    }
    const [layoutKey, method] = row.dataset.row.split("-");
    state.layout = layoutKey;
    state.method = method;
    refreshSelection();
  });

  const applySize = () => {
    const width = normalizeOddDimension(elements.widthInput, 65, 321);
    const height = normalizeOddDimension(elements.heightInput, 65, 217);
    if (width === state.width && height === state.height) {
      return;
    }
    state.width = width;
    state.height = height;
    buildExperiment();
    renderAll();
  };

  elements.widthInput.addEventListener("change", applySize);
  elements.heightInput.addEventListener("change", applySize);
  elements.widthInput.addEventListener("blur", applySize);
  elements.heightInput.addEventListener("blur", applySize);

  elements.maskToggle.addEventListener("change", () => {
    state.maskVisible = elements.maskToggle.checked;
    elements.edgeCanvas.classList.toggle("is-hidden", !state.maskVisible);
  });

  elements.regenerateButton.addEventListener("click", () => {
    state.seed += 1;
    elements.regenerateButton.classList.add("is-spinning");
    window.setTimeout(() => elements.regenerateButton.classList.remove("is-spinning"), 360);
    buildExperiment();
    renderAll();
  });
}

function initialize() {
  cacheElements();
  buildExperiment();
  bindEvents();
  renderAll();
}

initialize();
