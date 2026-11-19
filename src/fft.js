const TAU = Math.PI * 2;

function transform1D(real, imag, offset, stride, size, inverse) {
  let j = 0;
  for (let i = 1; i < size; i += 1) {
    let bit = size >> 1;
    while (j & bit) {
      j ^= bit;
      bit >>= 1;
    }
    j ^= bit;
    if (i < j) {
      const ai = offset + i * stride;
      const aj = offset + j * stride;
      const tr = real[ai];
      const ti = imag[ai];
      real[ai] = real[aj];
      imag[ai] = imag[aj];
      real[aj] = tr;
      imag[aj] = ti;
    }
  }

  for (let length = 2; length <= size; length <<= 1) {
    const angle = (inverse ? TAU : -TAU) / length;
    const wLengthReal = Math.cos(angle);
    const wLengthImag = Math.sin(angle);
    const half = length >> 1;
    for (let start = 0; start < size; start += length) {
      let wReal = 1;
      let wImag = 0;
      for (let k = 0; k < half; k += 1) {
        const even = offset + (start + k) * stride;
        const odd = offset + (start + k + half) * stride;
        const oddReal = real[odd] * wReal - imag[odd] * wImag;
        const oddImag = real[odd] * wImag + imag[odd] * wReal;
        real[odd] = real[even] - oddReal;
        imag[odd] = imag[even] - oddImag;
        real[even] += oddReal;
        imag[even] += oddImag;
        const nextReal = wReal * wLengthReal - wImag * wLengthImag;
        wImag = wReal * wLengthImag + wImag * wLengthReal;
        wReal = nextReal;
      }
    }
  }
}

export function fft2D(real, imag, size, inverse = false) {
  for (let y = 0; y < size; y += 1) {
    transform1D(real, imag, y * size, 1, size, inverse);
  }
  for (let x = 0; x < size; x += 1) {
    transform1D(real, imag, x, size, size, inverse);
  }
  if (inverse) {
    const scale = 1 / (size * size);
    for (let i = 0; i < real.length; i += 1) {
      real[i] *= scale;
      imag[i] *= scale;
    }
  }
}

export function fftShift(real, imag, size) {
  const shiftedReal = new Float64Array(real.length);
  const shiftedImag = new Float64Array(imag.length);
  const half = size >> 1;
  for (let y = 0; y < size; y += 1) {
    const sy = (y + half) % size;
    for (let x = 0; x < size; x += 1) {
      const sx = (x + half) % size;
      const src = sy * size + sx;
      const dst = y * size + x;
      shiftedReal[dst] = real[src];
      shiftedImag[dst] = imag[src];
    }
  }
  return { real: shiftedReal, imag: shiftedImag };
}

export function ifftShift(real, imag, size) {
  return fftShift(real, imag, size);
}

export function forwardSpectrum(values, size, { window = true, removeMean = true } = {}) {
  const real = new Float64Array(values.length);
  const imag = new Float64Array(values.length);
  let mean = 0;
  if (removeMean) {
    for (let i = 0; i < values.length; i += 1) {
      mean += values[i];
    }
    mean /= values.length;
  }

  for (let y = 0; y < size; y += 1) {
    const wy = window ? 0.5 - 0.5 * Math.cos((TAU * y) / (size - 1)) : 1;
    for (let x = 0; x < size; x += 1) {
      const wx = window ? 0.5 - 0.5 * Math.cos((TAU * x) / (size - 1)) : 1;
      real[y * size + x] = (values[y * size + x] - mean) * wx * wy;
    }
  }

  fft2D(real, imag, size, false);
  return fftShift(real, imag, size);
}

export function inverseFromShifted(real, imag, size, mask) {
  const filteredReal = new Float64Array(real.length);
  const filteredImag = new Float64Array(imag.length);
  for (let i = 0; i < real.length; i += 1) {
    const attenuation = mask ? mask[i] : 1;
    filteredReal[i] = real[i] * attenuation;
    filteredImag[i] = imag[i] * attenuation;
  }
  const unshifted = ifftShift(filteredReal, filteredImag, size);
  fft2D(unshifted.real, unshifted.imag, size, true);
  return unshifted.real;
}

export function magnitudeFromSpectrum(real, imag, size) {
  const magnitude = new Float32Array(real.length);
  let max = 0;
  for (let i = 0; i < real.length; i += 1) {
    const value = Math.hypot(real[i], imag[i]);
    magnitude[i] = value;
    if (value > max) max = value;
  }
  return { magnitude, max };
}

export function frequencyAt(index, size) {
  const half = size >> 1;
  return (index - half) / size;
}

export function indexAtFrequency(frequency, size) {
  return Math.round(frequency * size + (size >> 1));
}
