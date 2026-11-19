function clamp01(value) {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

function mix(base, target, amount) {
  return base + (target - base) * amount;
}

function put(planes, width, x, y, r, g, b) {
  const index = y * width + x;
  planes.r[index] = clamp01(r);
  planes.g[index] = clamp01(g);
  planes.b[index] = clamp01(b);
}

function add(planes, width, x, y, r, g, b) {
  const index = y * width + x;
  planes.r[index] = clamp01(planes.r[index] + r);
  planes.g[index] = clamp01(planes.g[index] + g);
  planes.b[index] = clamp01(planes.b[index] + b);
}

function hsv(hue, saturation, value) {
  const h = ((hue % 1) + 1) % 1;
  const i = Math.floor(h * 6);
  const f = h * 6 - i;
  const p = value * (1 - saturation);
  const q = value * (1 - f * saturation);
  const t = value * (1 - (1 - f) * saturation);
  return [
    [value, t, p],
    [q, value, p],
    [p, value, t],
    [p, q, value],
    [t, p, value],
    [value, p, q],
  ][i % 6];
}

export function createTestScene(width = 144, height = 108) {
  const planes = {
    r: new Float32Array(width * height),
    g: new Float32Array(width * height),
    b: new Float32Array(width * height),
  };
  const cx = width * 0.44;
  const cy = height * 0.45;
  const ringRadius = Math.min(width, height) * 0.26;

  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const u = x / Math.max(1, width - 1);
      const v = y / Math.max(1, height - 1);
      let r = 0.025 + v * 0.03 + u * 0.018;
      let g = 0.038 + (1 - v) * 0.045 + u * 0.015;
      let b = 0.065 + v * 0.07 + (1 - u) * 0.02;

      const glow = Math.max(0, 1 - Math.hypot((x - width * 0.18) / 42, (y - height * 0.22) / 32));
      r += glow * 0.075;
      g += glow * 0.036;
      b += glow * 0.012;

      const dx = x - cx;
      const dy = y - cy;
      const radius = Math.hypot(dx, dy);
      if (radius < ringRadius) {
        const angle = (Math.atan2(dy, dx) / (Math.PI * 2) + 1) % 1;
        const [hr, hg, hb] = hsv(angle + 0.53, 0.74, 0.76);
        const radialFade = 0.45 + 0.55 * (1 - radius / ringRadius);
        r = mix(r, hr, radialFade);
        g = mix(g, hg, radialFade);
        b = mix(b, hb, radialFade);
      }
      if (Math.abs(radius - ringRadius) < 0.72) {
        r = 0.82;
        g = 0.9;
        b = 0.96;
      }
      put(planes, width, x, y, r, g, b);
    }
  }

  for (let y = 8; y < 38; y += 1) {
    for (let x = 8; x < 27; x += 1) {
      if ((x + y * 3) % 4 < 2) put(planes, width, x, y, 0.92, 0.92, 0.92);
      else put(planes, width, x, y, 0.015, 0.018, 0.025);
    }
  }

  for (let y = 8; y < 38; y += 1) {
    for (let x = 8; x < 27; x += 1) {
      if (x === 9 || x === 26) put(planes, width, x, y, 0.12, 0.18, 0.22);
      if (y === 8 || y === 37) put(planes, width, x, y, 0.12, 0.18, 0.22);
    }
  }

  const bands = [
    { offset: -20, color: [0.95, 0.16, 0.24] },
    { offset: -17, color: [0.16, 0.9, 0.48] },
    { offset: -14, color: [0.2, 0.48, 1] },
    { offset: -11, color: [0.98, 0.82, 0.12] },
    { offset: -8, color: [0.98, 0.98, 0.98] },
  ];
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const carrier = x * 0.82 + y * 0.57;
      for (const band of bands) {
        const d = Math.abs(carrier - (band.offset + width * 0.92));
        if (d < 0.48) {
          put(planes, width, x, y, ...band.color);
        }
      }
    }
  }

  const stripeColors = [
    [0.96, 0.11, 0.16],
    [0.1, 0.93, 0.3],
    [0.1, 0.35, 1],
    [0.98, 0.86, 0.08],
    [0.95, 0.1, 0.86],
    [0.03, 0.92, 0.93],
  ];
  for (let y = 51; y < 79; y += 1) {
    for (let x = 105; x < 136; x += 1) {
      const stripe = Math.floor((x - 105) / 1.5);
      const color = stripeColors[stripe % stripeColors.length];
      if ((x - 105) % 3 !== 2) put(planes, width, x, y, ...color);
      else put(planes, width, x, y, 0.018, 0.025, 0.035);
    }
  }

  for (let y = 88; y < 103; y += 1) {
    for (let x = 44; x < 92; x += 1) {
      const phase = Math.floor((y - 88) / 1.4);
      if (phase % 2 === 0) {
        const t = (x - 44) / 48;
        put(planes, width, x, y, mix(0.94, 0.1, t), mix(0.16, 0.9, t), mix(0.12, 0.95, t));
      } else {
        put(planes, width, x, y, 0.02, 0.028, 0.042);
      }
    }
  }

  const lights = [
    [96, 24, 2.2, [1, 1, 1]],
    [121, 91, 2.6, [1, 0.14, 0.12]],
    [36, 87, 1.8, [0.98, 0.95, 0.22]],
    [130, 31, 1.5, [0.15, 0.82, 1]],
    [68, 76, 1.4, [1, 1, 1]],
  ];
  for (const [lx, ly, lightRadius, color] of lights) {
    for (let y = Math.floor(ly - 5); y <= Math.ceil(ly + 5); y += 1) {
      for (let x = Math.floor(lx - 5); x <= Math.ceil(lx + 5); x += 1) {
        if (x < 0 || y < 0 || x >= width || y >= height) continue;
        const distance = Math.hypot(x - lx, y - ly);
        if (distance <= lightRadius) {
          put(planes, width, x, y, ...color);
        } else if (distance < lightRadius + 2.4) {
          const amount = Math.pow(1 - (distance - lightRadius) / 2.4, 2) * 0.35;
          add(planes, width, x, y, color[0] * amount, color[1] * amount, color[2] * amount);
        }
      }
    }
  }

  return planes;
}
