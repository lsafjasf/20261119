(function (global) {
  "use strict";

  const N = 8;
  const A = new Float64Array(N * N);

  for (let u = 0; u < N; u += 1) {
    const scale = u === 0 ? 1 / Math.sqrt(2) : 1;
    for (let x = 0; x < N; x += 1) {
      A[u * N + x] = 0.5 * scale * Math.cos(((2 * x + 1) * u * Math.PI) / 16);
    }
  }

  const LUMA_BASE = [
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77,
    24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103, 99,
  ];

  const CHROMA_BASE = [
    17, 18, 24, 47, 99, 99, 99, 99,
    18, 21, 26, 66, 99, 99, 99, 99,
    24, 26, 56, 99, 99, 99, 99, 99,
    47, 66, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
  ];

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function roundHalfAway(value) {
    return value < 0 ? -Math.floor(-value + 0.5) : Math.floor(value + 0.5);
  }

  function transformRows(source, output) {
    for (let y = 0; y < N; y += 1) {
      for (let u = 0; u < N; u += 1) {
        let sum = 0;
        for (let x = 0; x < N; x += 1) {
          sum += source[y * N + x] * A[u * N + x];
        }
        output[y * N + u] = sum;
      }
    }
  }

  function transformColumns(source, output) {
    for (let v = 0; v < N; v += 1) {
      for (let u = 0; u < N; u += 1) {
        let sum = 0;
        for (let y = 0; y < N; y += 1) {
          sum += source[y * N + u] * A[v * N + y];
        }
        output[v * N + u] = sum;
      }
    }
  }

  function fdctBlock(values, levelShift) {
    const shift = levelShift === undefined ? 128 : levelShift;
    const shifted = new Float64Array(N * N);
    const temp = new Float64Array(N * N);
    const result = new Float64Array(N * N);

    for (let i = 0; i < N * N; i += 1) {
      shifted[i] = values[i] - shift;
    }
    transformRows(shifted, temp);
    transformColumns(temp, result);
    return result;
  }

  function idctBlock(coeffs, levelShift) {
    const shift = levelShift === undefined ? 128 : levelShift;
    const temp = new Float64Array(N * N);
    const spatial = new Float64Array(N * N);

    for (let y = 0; y < N; y += 1) {
      for (let u = 0; u < N; u += 1) {
        let sum = 0;
        for (let v = 0; v < N; v += 1) {
          sum += coeffs[v * N + u] * A[v * N + y];
        }
        temp[y * N + u] = sum;
      }
    }

    for (let y = 0; y < N; y += 1) {
      for (let x = 0; x < N; x += 1) {
        let sum = 0;
        for (let u = 0; u < N; u += 1) {
          sum += temp[y * N + u] * A[u * N + x];
        }
        spatial[y * N + x] = sum + shift;
      }
    }
    return spatial;
  }

  function quantize(coeffs, table) {
    const result = new Int32Array(N * N);
    for (let i = 0; i < N * N; i += 1) {
      const step = Math.max(1, table[i]);
      result[i] = roundHalfAway(coeffs[i] / step);
    }
    return result;
  }

  function dequantize(quantized, table) {
    const result = new Float64Array(N * N);
    for (let i = 0; i < N * N; i += 1) {
      result[i] = quantized[i] * Math.max(1, table[i]);
    }
    return result;
  }

  function roundPixels(spatial) {
    const result = new Uint8Array(N * N);
    for (let i = 0; i < N * N; i += 1) {
      result[i] = clamp(roundHalfAway(spatial[i]), 0, 255);
    }
    return result;
  }

  function metricsBetween(original, reconstructed) {
    let sum = 0;
    let sumSquared = 0;
    let max = 0;
    let changed = 0;
    for (let i = 0; i < original.length; i += 1) {
      const error = Math.abs(original[i] - reconstructed[i]);
      sum += error;
      sumSquared += error * error;
      max = Math.max(max, error);
      if (error > 0) changed += 1;
    }
    return {
      mae: sum / original.length,
      max,
      changed,
      mse: sumSquared / original.length,
    };
  }

  function reconstructBlock(values, table) {
    const coeffs = fdctBlock(values, 128);
    const quantized = quantize(coeffs, table);
    const dequantized = dequantize(quantized, table);
    const spatial = idctBlock(dequantized, 128);
    const reconstructed = roundPixels(spatial);
    const error = metricsBetween(values, reconstructed);

    return {
      coeffs,
      quantized,
      dequantized,
      spatial,
      reconstructed,
      error,
    };
  }

  function reconstructFromQuantized(values, quantized, table) {
    const coeffs = fdctBlock(values, 128);
    const dequantized = dequantize(quantized, table);
    const spatial = idctBlock(dequantized, 128);
    const reconstructed = roundPixels(spatial);
    return {
      coeffs,
      dequantized,
      spatial,
      reconstructed,
      error: metricsBetween(values, reconstructed),
    };
  }

  function qualityScale(quality) {
    const q = clamp(Math.round(quality), 1, 100);
    return q < 50 ? Math.floor(5000 / q) : 200 - q * 2;
  }

  function makeQuantTable(kind, quality) {
    const base = kind === "chroma" ? CHROMA_BASE : LUMA_BASE;
    const scale = qualityScale(quality);
    return base.map((value) => clamp(Math.floor((value * scale + 50) / 100), 1, 255));
  }

  function makeUnitTable() {
    return new Array(N * N).fill(1);
  }

  function bandEnergy(coeffs, dequantized) {
    const bands = [
      { id: "dc", label: "DC", range: "0", raw: 0, retained: 0 },
      { id: "low", label: "低频", range: "1–2", raw: 0, retained: 0 },
      { id: "mid", label: "中频", range: "3–5", raw: 0, retained: 0 },
      { id: "high", label: "高频", range: "6–7", raw: 0, retained: 0 },
    ];

    for (let v = 0; v < N; v += 1) {
      for (let u = 0; u < N; u += 1) {
        const index = v * N + u;
        const radius = Math.max(u, v);
        let band = bands[0];
        if (radius >= 6) band = bands[3];
        else if (radius >= 3) band = bands[2];
        else if (radius >= 1) band = bands[1];
        band.raw += coeffs[index] * coeffs[index];
        band.retained += dequantized[index] * dequantized[index];
      }
    }

    const rawTotal = bands.reduce((sum, band) => sum + band.raw, 0);
    const retainedTotal = bands.reduce((sum, band) => sum + band.retained, 0);
    bands.forEach((band) => {
      band.rawShare = rawTotal > 0 ? band.raw / rawTotal : 0;
      band.retainedShare = rawTotal > 0 ? band.retained / rawTotal : 0;
    });

    return {
      bands,
      rawTotal,
      retainedTotal,
      retainedRatio: rawTotal > 0 ? clamp(retainedTotal / rawTotal, 0, 1.5) : 1,
      highRawShare: rawTotal > 0 ? bands[3].raw / rawTotal : 0,
    };
  }

  function boundaryError(original, reconstructed, width, height, blockSize) {
    const step = blockSize || N;
    let sum = 0;
    let max = 0;
    let count = 0;

    for (let y = 0; y < height; y += 1) {
      for (let x = step; x < width; x += step) {
        const originalStep = original[y * width + x] - original[y * width + x - 1];
        const reconstructedStep =
          reconstructed[y * width + x] - reconstructed[y * width + x - 1];
        const error = Math.abs(reconstructedStep - originalStep);
        sum += error;
        max = Math.max(max, error);
        count += 1;
      }
    }

    for (let y = step; y < height; y += step) {
      for (let x = 0; x < width; x += 1) {
        const originalStep = original[y * width + x] - original[(y - 1) * width + x];
        const reconstructedStep =
          reconstructed[y * width + x] - reconstructed[(y - 1) * width + x];
        const error = Math.abs(reconstructedStep - originalStep);
        sum += error;
        max = Math.max(max, error);
        count += 1;
      }
    }

    return { mae: count > 0 ? sum / count : 0, max, count };
  }

  function blockBoundaryError(
    original,
    reconstructed,
    width,
    height,
    blockX,
    blockY,
    blockSize,
  ) {
    const step = blockSize || N;
    const left = blockX * step;
    const top = blockY * step;
    const right = left + step;
    const bottom = top + step;
    let sum = 0;
    let max = 0;
    let count = 0;

    function compare(x1, y1, x2, y2) {
      const firstOriginal = original[y1 * width + x1] - original[y2 * width + x2];
      const firstReconstructed =
        reconstructed[y1 * width + x1] - reconstructed[y2 * width + x2];
      const error = Math.abs(firstReconstructed - firstOriginal);
      sum += error;
      max = Math.max(max, error);
      count += 1;
    }

    for (let y = top; y < bottom; y += 1) {
      if (left > 0) compare(left, y, left - 1, y);
      if (right < width) compare(right - 1, y, right, y);
    }
    for (let x = left; x < right; x += 1) {
      if (top > 0) compare(x, top, x, top - 1);
      if (bottom < height) compare(x, bottom - 1, x, bottom);
    }

    return { mae: count > 0 ? sum / count : 0, max, count };
  }

  function planePsnr(original, reconstructed) {
    let squaredError = 0;
    for (let i = 0; i < original.length; i += 1) {
      const diff = original[i] - reconstructed[i];
      squaredError += diff * diff;
    }
    const mse = squaredError / original.length;
    return mse === 0 ? Infinity : 10 * Math.log10((255 * 255) / mse);
  }

  function basisContribution(v, u, x, y) {
    return A[u * N + x] * A[v * N + y];
  }

  const api = {
    N,
    A,
    LUMA_BASE,
    CHROMA_BASE,
    clamp,
    roundHalfAway,
    fdctBlock,
    idctBlock,
    quantize,
    dequantize,
    roundPixels,
    metricsBetween,
    reconstructBlock,
    reconstructFromQuantized,
    qualityScale,
    makeQuantTable,
    makeUnitTable,
    bandEnergy,
    boundaryError,
    blockBoundaryError,
    planePsnr,
    basisContribution,
  };

  global.JpegMath = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
