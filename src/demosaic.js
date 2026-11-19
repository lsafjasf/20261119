export const PATTERNS = Object.freeze({
  RGGB: Object.freeze(["R", "G", "G", "B"]),
  BGGR: Object.freeze(["B", "G", "G", "R"]),
});

export const CHANNEL_INDEX = Object.freeze({ R: 0, G: 1, B: 2 });

export function assertPattern(pattern) {
  if (!PATTERNS[pattern]) {
    throw new RangeError(`Unsupported Bayer pattern: ${pattern}`);
  }
  return pattern;
}

export function cfaAt(pattern, x, y) {
  assertPattern(pattern);
  return PATTERNS[pattern][(y & 1) * 2 + (x & 1)];
}

export function mosaicFromRgb(rgb, width, height, pattern) {
  assertPattern(pattern);
  const raw = new Float32Array(width * height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const color = cfaAt(pattern, x, y);
      const plane = color === "R" ? rgb.r : color === "G" ? rgb.g : rgb.b;
      raw[y * width + x] = plane[y * width + x];
    }
  }
  return raw;
}

export function createRgbPlanes(width, height) {
  return {
    r: new Float32Array(width * height),
    g: new Float32Array(width * height),
    b: new Float32Array(width * height),
  };
}

function clamp01(value) {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

function safeIndex(width, height, x, y) {
  return x >= 0 && y >= 0 && x < width && y < height ? y * width + x : -1;
}

function readRaw(raw, width, height, x, y) {
  const index = safeIndex(width, height, x, y);
  return index === -1 ? null : raw[index];
}

function average(values) {
  let total = 0;
  let count = 0;
  for (const value of values) {
    if (value !== null && value !== undefined) {
      total += value;
      count += 1;
    }
  }
  return count ? total / count : 0;
}

function orthogonalSites(x, y, color, pattern, width, height) {
  const sites = [];
  for (const [dx, dy] of [[0, -1], [1, 0], [0, 1], [-1, 0]]) {
    const nx = x + dx;
    const ny = y + dy;
    if (nx >= 0 && ny >= 0 && nx < width && ny < height && cfaAt(pattern, nx, ny) === color) {
      sites.push([nx, ny]);
    }
  }
  return sites;
}

function diagonalSites(x, y, color, pattern, width, height) {
  const sites = [];
  for (const [dx, dy] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
    const nx = x + dx;
    const ny = y + dy;
    if (nx >= 0 && ny >= 0 && nx < width && ny < height && cfaAt(pattern, nx, ny) === color) {
      sites.push([nx, ny]);
    }
  }
  return sites;
}

export function interpolateBilinearPixel(raw, width, height, pattern, x, y) {
  const color = cfaAt(pattern, x, y);
  const center = readRaw(raw, width, height, x, y);
  const value = { R: null, G: null, B: null };
  value[color] = center;

  if (color === "G") {
    const redSites = orthogonalSites(x, y, "R", pattern, width, height);
    const blueSites = orthogonalSites(x, y, "B", pattern, width, height);
    value.R = average(redSites.map(([nx, ny]) => readRaw(raw, width, height, nx, ny)));
    value.B = average(blueSites.map(([nx, ny]) => readRaw(raw, width, height, nx, ny)));
  } else {
    const greenSites = orthogonalSites(x, y, "G", pattern, width, height);
    const opposite = color === "R" ? "B" : "R";
    const oppositeSites = diagonalSites(x, y, opposite, pattern, width, height);
    value.G = average(greenSites.map(([nx, ny]) => readRaw(raw, width, height, nx, ny)));
    value[opposite] = average(
      oppositeSites.map(([nx, ny]) => readRaw(raw, width, height, nx, ny)),
    );
  }

  return {
    R: clamp01(value.R ?? 0),
    G: clamp01(value.G ?? 0),
    B: clamp01(value.B ?? 0),
  };
}

export function demosaicBilinear(raw, width, height, pattern) {
  assertPattern(pattern);
  const out = createRgbPlanes(width, height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const index = y * width + x;
      const pixel = interpolateBilinearPixel(raw, width, height, pattern, x, y);
      out.r[index] = pixel.R;
      out.g[index] = pixel.G;
      out.b[index] = pixel.B;
    }
  }
  return out;
}

function addTerm(terms, x, y, weight) {
  if (!weight) return;
  const key = `${x},${y}`;
  const existing = terms.get(key);
  if (existing) {
    existing.weight += weight;
  } else {
    terms.set(key, { x, y, weight });
  }
}

function mergeTerms(target, source, scale = 1) {
  for (const term of source.values()) {
    addTerm(target, term.x, term.y, term.weight * scale);
  }
}

function directionalGreen(raw, width, height, pattern, x, y, direction) {
  const terms = new Map();
  const center = raw[y * width + x];
  if (direction === "H") {
    addTerm(terms, x - 1, y, 0.5);
    addTerm(terms, x + 1, y, 0.5);
    if (safeIndex(width, height, x - 2, y) !== -1 && safeIndex(width, height, x + 2, y) !== -1) {
      addTerm(terms, x, y, 0.5);
      addTerm(terms, x - 2, y, -0.25);
      addTerm(terms, x + 2, y, -0.25);
    }
  } else {
    addTerm(terms, x, y - 1, 0.5);
    addTerm(terms, x, y + 1, 0.5);
    if (safeIndex(width, height, x, y - 2) !== -1 && safeIndex(width, height, x, y + 2) !== -1) {
      addTerm(terms, x, y, 0.5);
      addTerm(terms, x, y - 2, -0.25);
      addTerm(terms, x, y + 2, -0.25);
    }
  }
  return terms;
}

export function traceGreen(raw, width, height, pattern, x, y) {
  const terms = new Map();
  if (cfaAt(pattern, x, y) === "G") {
    addTerm(terms, x, y, 1);
    return terms;
  }

  const left = safeIndex(width, height, x - 1, y) !== -1;
  const right = safeIndex(width, height, x + 1, y) !== -1;
  const top = safeIndex(width, height, x, y - 1) !== -1;
  const bottom = safeIndex(width, height, x, y + 1) !== -1;
  const canH = left && right;
  const canV = top && bottom;

  if (canH && canV) {
    const center = raw[y * width + x];
    let gradH = Math.abs(raw[y * width + x - 1] - raw[y * width + x + 1]);
    let gradV = Math.abs(raw[(y - 1) * width + x] - raw[(y + 1) * width + x]);
    if (x > 1 && x < width - 2) {
      gradH += Math.abs(2 * center - raw[y * width + x - 2] - raw[y * width + x + 2]);
    }
    if (y > 1 && y < height - 2) {
      gradV += Math.abs(2 * center - raw[(y - 2) * width + x] - raw[(y + 2) * width + x]);
    }

    if (gradH < gradV * 0.92) {
      return directionalGreen(raw, width, height, pattern, x, y, "H");
    }
    if (gradV < gradH * 0.92) {
      return directionalGreen(raw, width, height, pattern, x, y, "V");
    }

    mergeTerms(terms, directionalGreen(raw, width, height, pattern, x, y, "H"), 0.5);
    mergeTerms(terms, directionalGreen(raw, width, height, pattern, x, y, "V"), 0.5);
    return terms;
  }

  if (canH) {
    addTerm(terms, x - 1, y, 0.5);
    addTerm(terms, x + 1, y, 0.5);
    return terms;
  }
  if (canV) {
    addTerm(terms, x, y - 1, 0.5);
    addTerm(terms, x, y + 1, 0.5);
    return terms;
  }

  const greenSites = [
    ...orthogonalSites(x, y, "G", pattern, width, height),
    ...diagonalSites(x, y, "G", pattern, width, height),
  ];
  if (greenSites.length) {
    for (const [nx, ny] of greenSites) addTerm(terms, nx, ny, 1 / greenSites.length);
  } else {
    addTerm(terms, x, y, 1);
  }
  return terms;
}

function traceGreenValue(raw, width, height, pattern, x, y) {
  const terms = traceGreen(raw, width, height, pattern, x, y);
  let total = 0;
  for (const term of terms.values()) {
    total += raw[term.y * width + term.x] * term.weight;
  }
  return total;
}

function colorDifferenceEstimate(raw, width, height, pattern, x, y, channel) {
  const green = traceGreenValue(raw, width, height, pattern, x, y);
  const sites = cfaAt(pattern, x, y) === "G"
    ? orthogonalSites(x, y, channel, pattern, width, height)
    : diagonalSites(x, y, channel, pattern, width, height);
  let difference = 0;
  for (const [nx, ny] of sites) {
    difference += raw[ny * width + nx] - traceGreenValue(raw, width, height, pattern, nx, ny);
  }
  return green + (sites.length ? difference / sites.length : 0);
}

export function interpolateEdgeDirectedPixel(raw, width, height, pattern, x, y) {
  const color = cfaAt(pattern, x, y);
  const center = raw[y * width + x];
  const value = { R: null, G: null, B: null };

  if (color === "G") {
    value.G = center;
    value.R = colorDifferenceEstimate(raw, width, height, pattern, x, y, "R");
    value.B = colorDifferenceEstimate(raw, width, height, pattern, x, y, "B");
  } else {
    value[color] = center;
    value.G = clamp01(traceGreenValue(raw, width, height, pattern, x, y));
    value[color === "R" ? "B" : "R"] = colorDifferenceEstimate(
      raw,
      width,
      height,
      pattern,
      x,
      y,
      color === "R" ? "B" : "R",
    );
  }

  return {
    R: clamp01(value.R ?? 0),
    G: clamp01(value.G ?? 0),
    B: clamp01(value.B ?? 0),
  };
}

export function demosaicEdgeDirected(raw, width, height, pattern) {
  assertPattern(pattern);
  const out = createRgbPlanes(width, height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const index = y * width + x;
      const pixel = interpolateEdgeDirectedPixel(raw, width, height, pattern, x, y);
      out.r[index] = pixel.R;
      out.g[index] = pixel.G;
      out.b[index] = pixel.B;
    }
  }
  return out;
}

function buildBilinearTerms(width, height, pattern, x, y, channel) {
  const terms = new Map();
  const color = cfaAt(pattern, x, y);
  if (color === channel) {
    addTerm(terms, x, y, 1);
    return terms;
  }

  let sites;
  if (color === "G") {
    sites = orthogonalSites(x, y, channel, pattern, width, height);
  } else if (channel === "G") {
    sites = orthogonalSites(x, y, "G", pattern, width, height);
  } else {
    sites = diagonalSites(x, y, channel, pattern, width, height);
  }
  if (sites.length) {
    for (const [nx, ny] of sites) addTerm(terms, nx, ny, 1 / sites.length);
  }
  return terms;
}

function buildEdgeTerms(raw, width, height, pattern, x, y, channel) {
  const terms = new Map();
  const color = cfaAt(pattern, x, y);
  if (color === channel) {
    addTerm(terms, x, y, 1);
    return terms;
  }
  if (color === "G" && channel === "G") {
    addTerm(terms, x, y, 1);
    return terms;
  }

  if (color !== "G" && channel === "G") {
    mergeTerms(terms, traceGreen(raw, width, height, pattern, x, y));
    return terms;
  }

  const isGreenSite = color === "G";
  const sites = isGreenSite
    ? orthogonalSites(x, y, channel, pattern, width, height)
    : diagonalSites(x, y, channel, pattern, width, height);
  mergeTerms(terms, traceGreen(raw, width, height, pattern, x, y));
  if (!sites.length) return terms;

  for (const [nx, ny] of sites) {
    addTerm(terms, nx, ny, 1 / sites.length);
    mergeTerms(terms, traceGreen(raw, width, height, pattern, nx, ny), -1 / sites.length);
  }
  return terms;
}

export function interpolationWeights(raw, width, height, pattern, x, y, method, channel) {
  assertPattern(pattern);
  if (!CHANNEL_INDEX.hasOwnProperty(channel)) {
    throw new RangeError(`Unknown channel: ${channel}`);
  }
  if (x < 0 || y < 0 || x >= width || y >= height) {
    throw new RangeError("Pixel is outside the image");
  }

  const terms = method === "edge"
    ? buildEdgeTerms(raw, width, height, pattern, x, y, channel)
    : buildBilinearTerms(width, height, pattern, x, y, channel);
  const entries = [...terms.values()]
    .filter((term) => Math.abs(term.weight) > 1e-8)
    .sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight));
  return {
    entries,
    sum: entries.reduce((total, term) => total + term.weight, 0),
  };
}

export function explainPixel(raw, width, height, pattern, x, y) {
  const bilinear = interpolateBilinearPixel(raw, width, height, pattern, x, y);
  const edge = interpolateEdgeDirectedPixel(raw, width, height, pattern, x, y);
  return {
    x,
    y,
    cfa: cfaAt(pattern, x, y),
    raw: raw[y * width + x],
    bilinear,
    edge,
  };
}
