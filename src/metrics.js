function clamp01(value) {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

function luma(r, g, b) {
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function computeMetrics(truth, recon, width, height) {
  const count = width * height;
  const errors = {
    r: new Float32Array(count),
    g: new Float32Array(count),
    b: new Float32Array(count),
  };
  const chromaError = new Float32Array(count);
  const greenError = new Float32Array(count);
  const truthLuma = new Float32Array(count);
  let rSum = 0;
  let gSum = 0;
  let bSum = 0;
  let squared = 0;

  for (let i = 0; i < count; i += 1) {
    const er = Math.abs(truth.r[i] - recon.r[i]);
    const eg = Math.abs(truth.g[i] - recon.g[i]);
    const eb = Math.abs(truth.b[i] - recon.b[i]);
    errors.r[i] = er;
    errors.g[i] = eg;
    errors.b[i] = eb;
    rSum += er;
    gSum += eg;
    bSum += eb;
    squared += er * er + eg * eg + eb * eb;
    const truthCr = truth.r[i] - truth.g[i];
    const truthCb = truth.b[i] - truth.g[i];
    const reconCr = recon.r[i] - recon.g[i];
    const reconCb = recon.b[i] - recon.g[i];
    chromaError[i] = 0.5 * (Math.abs(truthCr - reconCr) + Math.abs(truthCb - reconCb));
    greenError[i] = recon.g[i] - truth.g[i];
    truthLuma[i] = luma(truth.r[i], truth.g[i], truth.b[i]);
  }

  const edgeMask = new Uint8Array(count);
  const edgeDilated = new Uint8Array(count);
  let edgeCount = 0;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const i = y * width + x;
      const left = x > 0 ? truthLuma[i - 1] : truthLuma[i];
      const right = x < width - 1 ? truthLuma[i + 1] : truthLuma[i];
      const top = y > 0 ? truthLuma[i - width] : truthLuma[i];
      const bottom = y < height - 1 ? truthLuma[i + width] : truthLuma[i];
      if (Math.max(Math.abs(left - right), Math.abs(top - bottom)) > 0.055) {
        edgeMask[i] = 1;
        edgeCount += 1;
      }
    }
  }
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      if (!edgeMask[y * width + x]) continue;
      for (let dy = -1; dy <= 1; dy += 1) {
        for (let dx = -1; dx <= 1; dx += 1) {
          const nx = x + dx;
          const ny = y + dy;
          if (nx >= 0 && ny >= 0 && nx < width && ny < height) edgeDilated[ny * width + nx] = 1;
        }
      }
    }
  }

  const fringe = new Float32Array(count);
  const zipper = new Float32Array(count);
  let fringePixels = 0;
  let zipperTotal = 0;
  let edgeArea = 0;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const i = y * width + x;
      if (!edgeDilated[i]) continue;
      edgeArea += 1;
      if (chromaError[i] > 0.025) fringePixels += 1;
      fringe[i] = clamp01(chromaError[i] * 3.2);

      const left = x > 0 ? greenError[i - 1] : greenError[i];
      const right = x < width - 1 ? greenError[i + 1] : greenError[i];
      const top = y > 0 ? greenError[i - width] : greenError[i];
      const bottom = y < height - 1 ? greenError[i + width] : greenError[i];
      const horizontal = Math.abs(left - 2 * greenError[i] + right);
      const vertical = Math.abs(top - 2 * greenError[i] + bottom);
      const alternating = Math.max(horizontal, vertical);
      zipper[i] = clamp01(alternating * 4.5);
      zipperTotal += alternating;
    }
  }

  const rgbMae = (rSum + gSum + bSum) / (count * 3);
  const mse = squared / (count * 3);
  return {
    width,
    height,
    channelMae: { r: rSum / count, g: gSum / count, b: bSum / count },
    rgbMae,
    psnr: mse === 0 ? 99 : 10 * Math.log10(1 / mse),
    fringeShare: edgeArea ? (fringePixels / edgeArea) * 100 : 0,
    fringeScore: [...fringe].reduce((sum, value) => sum + value, 0) / count,
    zipperScore: edgeArea ? (zipperTotal / edgeArea) * 100 : 0,
    edgeCount,
    errors,
    chromaError,
    greenError,
    edgeDilated,
    fringe,
    zipper,
  };
}

function toByte(value) {
  return Math.round(Math.max(0, Math.min(1, value)) * 255);
}

export function errorImage(truth, recon, width, height, metrics, mode, channel = "rgb") {
  const image = new Uint8ClampedArray(width * height * 4);
  for (let i = 0; i < width * height; i += 1) {
    const p = i * 4;
    if (mode === "error") {
      const er = Math.pow(metrics.errors.r[i] * 7, 0.72);
      const eg = Math.pow(metrics.errors.g[i] * 7, 0.72);
      const eb = Math.pow(metrics.errors.b[i] * 7, 0.72);
      if (channel === "r") {
        image[p] = toByte(er);
        image[p + 1] = toByte(er * 0.08);
        image[p + 2] = toByte(er * 0.05);
      } else if (channel === "g") {
        image[p] = toByte(eg * 0.06);
        image[p + 1] = toByte(eg);
        image[p + 2] = toByte(eg * 0.1);
      } else if (channel === "b") {
        image[p] = toByte(eb * 0.05);
        image[p + 1] = toByte(eb * 0.15);
        image[p + 2] = toByte(eb);
      } else {
        image[p] = toByte(er);
        image[p + 1] = toByte(eg);
        image[p + 2] = toByte(eb);
      }
    } else if (mode === "fringe") {
      const value = metrics.fringe[i];
      image[p] = toByte(value * 1.1 + 0.015);
      image[p + 1] = toByte(value * 0.08 + 0.015);
      image[p + 2] = toByte(value * 0.55 + 0.025);
    } else if (mode === "zipper") {
      const value = metrics.zipper[i];
      image[p] = toByte(value * 1.15 + 0.012);
      image[p + 1] = toByte(value * 0.55 + 0.01);
      image[p + 2] = toByte(value * 0.04 + 0.016);
    }
    image[p + 3] = 255;
  }
  return image;
}

export function rgbImage(planes, width, height) {
  const image = new Uint8ClampedArray(width * height * 4);
  for (let i = 0; i < width * height; i += 1) {
    const p = i * 4;
    image[p] = toByte(planes.r[i]);
    image[p + 1] = toByte(planes.g[i]);
    image[p + 2] = toByte(planes.b[i]);
    image[p + 3] = 255;
  }
  return image;
}

export function channelErrorImage(metrics, width, height, channel) {
  const image = new Uint8ClampedArray(width * height * 4);
  const channelColors = {
    r: [255, 66, 91],
    g: [53, 219, 132],
    b: [82, 145, 255],
  };
  const tint = channelColors[channel];
  for (let i = 0; i < width * height; i += 1) {
    const p = i * 4;
    const value = Math.pow(metrics.errors[channel][i] * 9, 0.7);
    image[p] = toByte((value * tint[0]) / 255);
    image[p + 1] = toByte((value * tint[1]) / 255);
    image[p + 2] = toByte((value * tint[2]) / 255);
    image[p + 3] = 255;
  }
  return image;
}
