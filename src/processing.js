import {
  fftShift,
  forwardSpectrum,
  inverseFromShifted,
  magnitudeFromSpectrum,
} from "./fft.js";

const TAU = Math.PI * 2;

export function rgbaToLuminance(rgba, size) {
  const luminance = new Float32Array(size * size);
  for (let i = 0; i < luminance.length; i += 1) {
    const p = i * 4;
    luminance[i] = rgba[p] * 0.2126 + rgba[p + 1] * 0.7152 + rgba[p + 2] * 0.0722;
  }
  return luminance;
}

export function analyzeLuminance(rgba, size) {
  const luminance = rgbaToLuminance(rgba, size);
  const spectrum = forwardSpectrum(luminance, size, { window: false, removeMean: false });
  const analysisSpectrum = forwardSpectrum(luminance, size, { window: true, removeMean: true });
  const { magnitude, max } = magnitudeFromSpectrum(
    analysisSpectrum.real,
    analysisSpectrum.imag,
    size,
  );
  const channels = [0, 1, 2].map((channel) => {
    const values = new Float32Array(size * size);
    for (let i = 0; i < values.length; i += 1) {
      values[i] = rgba[i * 4 + channel];
    }
    return forwardSpectrum(values, size, { window: false, removeMean: false });
  });
  return { luminance, spectrum, magnitude, max, channels };
}

function localProminence(logMagnitude, size, x, y, radius = 12) {
  let outer = 0;
  let count = 0;
  for (let i = 0; i < 24; i += 1) {
    const angle = (TAU * i) / 24;
    const sx = Math.round(x + Math.cos(angle) * radius);
    const sy = Math.round(y + Math.sin(angle) * radius);
    if (sx < 0 || sy < 0 || sx >= size || sy >= size) continue;
    outer += logMagnitude[sy * size + sx];
    count += 1;
  }
  return logMagnitude[y * size + x] - outer / Math.max(1, count);
}

function isLocalMaximum(logMagnitude, size, x, y, radius = 3) {
  const center = logMagnitude[y * size + x];
  for (let oy = -radius; oy <= radius; oy += 1) {
    for (let ox = -radius; ox <= radius; ox += 1) {
      if (ox === 0 && oy === 0) continue;
      if (logMagnitude[(y + oy) * size + x + ox] > center) return false;
    }
  }
  return true;
}

export function detectPeakCandidates(magnitude, size, limit = 20) {
  const logMagnitude = new Float32Array(magnitude.length);
  for (let i = 0; i < magnitude.length; i += 1) {
    logMagnitude[i] = Math.log1p(magnitude[i]);
  }
  const half = size >> 1;
  const candidates = [];
  for (let y = 10; y < size - 10; y += 1) {
    for (let x = 10; x < size - 10; x += 1) {
      const dx = x - half;
      const dy = y - half;
      const radius = Math.hypot(dx, dy);
      if (radius < 9 || radius > half - 12) continue;
      if (dy < 0 || (dy === 0 && dx <= 0)) continue;
      if (!isLocalMaximum(logMagnitude, size, x, y, 3)) continue;
      const prominence = localProminence(logMagnitude, size, x, y);
      if (prominence < 0.18) continue;
      candidates.push({
        x,
        y,
        fx: dx / size,
        fy: dy / size,
        prominence,
        magnitude: magnitude[y * size + x],
      });
    }
  }

  candidates.sort((a, b) => b.prominence - a.prominence);
  const picked = [];
  for (const candidate of candidates) {
    const tooClose = picked.some((item) => Math.hypot(item.x - candidate.x, item.y - candidate.y) < 7);
    if (!tooClose) picked.push(candidate);
    if (picked.length >= limit) break;
  }
  return picked;
}

export function findPeakNear(magnitude, size, fx, fy, searchRadius = 4) {
  const half = size >> 1;
  const targetX = Math.round(half + fx * size);
  const targetY = Math.round(half + fy * size);
  let best = null;
  for (let oy = -searchRadius; oy <= searchRadius; oy += 1) {
    for (let ox = -searchRadius; ox <= searchRadius; ox += 1) {
      const x = targetX + ox;
      const y = targetY + oy;
      if (x < 1 || y < 1 || x >= size - 1 || y >= size - 1) continue;
      const value = magnitude[y * size + x];
      if (!best || value > best.magnitude) {
        best = {
          x,
          y,
          fx: (x - half) / size,
          fy: (y - half) / size,
          magnitude: value,
        };
      }
    }
  }
  return best;
}

function smoothstep(edge0, edge1, value) {
  const t = Math.max(0, Math.min(1, (value - edge0) / Math.max(1e-8, edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

function notchAt(mask, size, cx, cy, radius, softness, depth) {
  const half = size >> 1;
  const inner = Math.max(0.5, radius * (1 - softness * 0.72));
  const outer = radius * (1 + softness * 1.35) + 0.7;
  const extent = Math.ceil(outer);
  const minX = Math.max(0, Math.floor(cx - extent));
  const maxX = Math.min(size - 1, Math.ceil(cx + extent));
  const minY = Math.max(0, Math.floor(cy - extent));
  const maxY = Math.min(size - 1, Math.ceil(cy + extent));
  for (let y = minY; y <= maxY; y += 1) {
    const dy = y - cy;
    for (let x = minX; x <= maxX; x += 1) {
      const dcDistance = Math.hypot(x - half, y - half);
      if (dcDistance < 3.2) continue;
      const distance = Math.hypot(x - cx, dy);
      if (distance > outer) continue;
      const falloff = distance <= inner ? 1 : 1 - smoothstep(inner, outer, distance);
      const attenuation = 1 - falloff * depth;
      const index = y * size + x;
      if (attenuation < mask[index]) mask[index] = attenuation;
    }
  }
}

export function buildAutoMask(size, peaks, { radius = 5, softness = 0.65, strength = 0.99 } = {}) {
  const mask = new Float32Array(size * size);
  mask.fill(1);
  const half = size >> 1;
  const depth = Math.max(0, Math.min(1, strength));
  for (const peak of peaks) {
    if (!peak.enabled) continue;
    const cx = half + peak.fx * size;
    const cy = half + peak.fy * size;
    notchAt(mask, size, cx, cy, radius, softness, depth);
    notchAt(mask, size, half - peak.fx * size, half - peak.fy * size, radius, softness, depth);
  }
  return mask;
}

export function combineMasks(autoMask, manualMask, size) {
  if (!manualMask) return autoMask;
  const combined = new Float32Array(size * size);
  for (let i = 0; i < combined.length; i += 1) {
    combined[i] = Math.min(autoMask[i], manualMask[i]);
  }
  return combined;
}

export function paintMask(mask, size, cx, cy, radius, restore = false) {
  const minX = Math.max(0, Math.floor(cx - radius));
  const maxX = Math.min(size - 1, Math.ceil(cx + radius));
  const minY = Math.max(0, Math.floor(cy - radius));
  const maxY = Math.min(size - 1, Math.ceil(cy + radius));
  const half = size >> 1;
  for (let y = minY; y <= maxY; y += 1) {
    const dy = y - cy;
    for (let x = minX; x <= maxX; x += 1) {
      if (Math.hypot(x - half, y - half) < 4) continue;
      const distance = Math.hypot(x - cx, dy);
      if (distance > radius) continue;
      const feather = 1 - smoothstep(radius * 0.68, radius, distance);
      const index = y * size + x;
      if (restore) {
        mask[index] = Math.max(mask[index], feather);
      } else {
        mask[index] = Math.min(mask[index], 1 - feather);
      }
    }
  }
}

export function reconstructRgba(source, size, analysis, mask) {
  const filteredChannels = analysis.channels.map((channel) =>
    inverseFromShifted(channel.real, channel.imag, size, mask),
  );
  const filteredLuminance = new Float32Array(size * size);
  const output = new Uint8ClampedArray(source.length);
  for (let i = 0; i < filteredLuminance.length; i += 1) {
    const p = i * 4;
    output[p] = filteredChannels[0][i];
    output[p + 1] = filteredChannels[1][i];
    output[p + 2] = filteredChannels[2][i];
    output[p + 3] = 255;
    filteredLuminance[i] =
      output[p] * 0.2126 + output[p + 1] * 0.7152 + output[p + 2] * 0.0722;
  }
  return { rgba: output, filteredLuminance };
}

function gradientSquared(values, size) {
  const gradient = new Float32Array(values.length);
  for (let y = 1; y < size - 1; y += 1) {
    for (let x = 1; x < size - 1; x += 1) {
      const index = y * size + x;
      const gx =
        values[index - 1] * -0.5 +
        values[index + 1] * 0.5;
      const gy =
        values[index - size] * -0.5 +
        values[index + size] * 0.5;
      gradient[index] = gx * gx + gy * gy;
    }
  }
  return gradient;
}

function laplacianRms(values, size) {
  let energy = 0;
  let count = 0;
  for (let y = 1; y < size - 1; y += 1) {
    for (let x = 1; x < size - 1; x += 1) {
      const index = y * size + x;
      const value =
        values[index - 1] +
        values[index + 1] +
        values[index - size] +
        values[index + size] -
        values[index] * 4;
      energy += value * value;
      count += 1;
    }
  }
  return Math.sqrt(energy / Math.max(1, count));
}

function percentileThreshold(values, ratio) {
  let max = 0;
  for (let i = 0; i < values.length; i += 1) {
    if (values[i] > max) max = values[i];
  }
  const scale = max > 0 ? max / 255 : 1;
  const bins = new Uint32Array(256);
  for (let i = 0; i < values.length; i += 1) {
    bins[Math.min(255, Math.floor((values[i] / scale) * 1))] += 1;
  }
  const target = values.length * ratio;
  let total = 0;
  for (let i = 255; i >= 0; i -= 1) {
    total += bins[i];
    if (total >= target) return i * scale;
  }
  return 0;
}

export function measureQuality({
  size,
  baseLuminance,
  sourceLuminance,
  reconstructedLuminance,
  spectrum,
  mask,
}) {
  let bandEnergy = 0;
  let residualBandEnergy = 0;
  for (let i = 0; i < mask.length; i += 1) {
    const weight = 1 - mask[i];
    if (weight <= 0.001) continue;
    const power = spectrum.real[i] * spectrum.real[i] + spectrum.imag[i] * spectrum.imag[i];
    bandEnergy += power * weight;
    residualBandEnergy += power * weight * mask[i] * mask[i];
  }
  const energyReduction = bandEnergy > 1e-9
    ? Math.max(0, Math.min(100, (1 - residualBandEnergy / bandEnergy) * 100))
    : 0;

  const baseGradient = gradientSquared(baseLuminance, size);
  const sourceGradient = gradientSquared(sourceLuminance, size);
  const reconstructedGradient = gradientSquared(reconstructedLuminance, size);
  const edgeThreshold = percentileThreshold(baseGradient, 0.18);
  let edgeBefore = 0;
  let edgeAfter = 0;
  let edgeCount = 0;
  for (let i = 0; i < baseGradient.length; i += 1) {
    if (baseGradient[i] < edgeThreshold) continue;
    edgeBefore += sourceGradient[i];
    edgeAfter += reconstructedGradient[i];
    edgeCount += 1;
  }
  let edgeBase = 0;
  for (let i = 0; i < baseGradient.length; i += 1) {
    if (baseGradient[i] >= edgeThreshold) edgeBase += baseGradient[i];
  }
  const edgeChange = edgeCount && edgeBase > 1e-8
    ? ((edgeAfter - edgeBase) / edgeBase) * 100
    : 0;

  const difference = new Float32Array(baseLuminance.length);
  for (let i = 0; i < difference.length; i += 1) {
    difference[i] = reconstructedLuminance[i] - baseLuminance[i];
  }
  const residualHigh = laplacianRms(difference, size);
  const baseHigh = laplacianRms(baseLuminance, size);
  const ringing = baseHigh > 1e-8 ? (residualHigh / baseHigh) * 100 : 0;

  return {
    energyReduction,
    edgeChange,
    ringing,
    residualHigh,
  };
}

export function maskCoverage(mask) {
  let blocked = 0;
  for (let i = 0; i < mask.length; i += 1) {
    if (mask[i] < 0.98) blocked += 1 - mask[i];
  }
  return (blocked / mask.length) * 100;
}
