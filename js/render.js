// Canvas rendering for the ordinary STFT and reassigned spectrograms.
// Both panels share identical time (s) and frequency (Hz) axis ranges so the
// coordinate units are preserved when the analysis window length changes.

// dB normalization shared by both panels of one result, keeping color scales
// directly comparable.
export function decibelGrid(result, gridKey, dynamicRangeDb = 80) {
  const grid = result[gridKey];
  let peak = 0;
  for (const v of grid) if (v > peak) peak = v;
  const floor = peak > 0 ? peak * 10 ** (-dynamicRangeDb / 10) : 1;
  const out = new Float32Array(grid.length);
  for (let i = 0; i < grid.length; i++) {
    if (grid[i] <= floor || grid[i] <= 0) {
      out[i] = 0;
    } else {
      const db = 10 * Math.log10(grid[i] / peak);
      out[i] = Math.max(0, Math.min(1, (db + dynamicRangeDb) / dynamicRangeDb));
    }
  }
  return out;
}

// Viridis-like perceptually uniform lookup (no external assets).
const VIRIDIS = [
  [68, 1, 84], [72, 40, 120], [62, 74, 137], [49, 104, 142],
  [38, 130, 142], [31, 158, 137], [53, 183, 121], [109, 205, 89],
  [180, 222, 44], [253, 231, 37]
];

function viridis(t) {
  const x = Math.min(0.999999, Math.max(0, t)) * (VIRIDIS.length - 1);
  const i = Math.floor(x);
  const f = x - i;
  const a = VIRIDIS[i];
  const b = VIRIDIS[i + 1];
  return [
    Math.round(a[0] + (b[0] - a[0]) * f),
    Math.round(a[1] + (b[1] - a[1]) * f),
    Math.round(a[2] + (b[2] - a[2]) * f)
  ];
}

export class SpectrogramView {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.margin = options.margin ?? { left: 64, right: 12, top: 28, bottom: 42 };
    this.onClick = options.onClick ?? null;
    this.hover = null;
    this._bind();
  }

  setResult(result, gridKey, title) {
    this.result = result;
    this.gridKey = gridKey;
    this.title = title;
    this.image = decibelGrid(result, gridKey);
    this.draw();
  }

  geometry() {
    const dpr = window.devicePixelRatio || 1;
    const cssW = this.canvas.clientWidth;
    const cssH = this.canvas.clientHeight;
    if (this.canvas.width !== Math.round(cssW * dpr)) {
      this.canvas.width = Math.round(cssW * dpr);
      this.canvas.height = Math.round(cssH * dpr);
    }
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const m = this.margin;
    return {
      cssW, cssH,
      plotX: m.left,
      plotY: m.top,
      plotW: cssW - m.left - m.right,
      plotH: cssH - m.top - m.bottom
    };
  }

  // Physical coordinate <-> pixel mapping (seconds and Hz).
  timeToX(t, g) {
    const duration = this.result.params.frames * this.result.params.hop / this.result.params.sampleRate;
    return g.plotX + (t / duration) * g.plotW;
  }

  freqToY(f, g) {
    const nyquist = this.result.params.sampleRate / 2;
    return g.plotY + g.plotH - (f / nyquist) * g.plotH;
  }

  xToTime(px, g) {
    const duration = this.result.params.frames * this.result.params.hop / this.result.params.sampleRate;
    return ((px - g.plotX) / g.plotW) * duration;
  }

  yToFreq(py, g) {
    const nyquist = this.result.params.sampleRate / 2;
    return ((g.plotY + g.plotH - py) / g.plotH) * nyquist;
  }

  draw() {
    if (!this.result) return;
    const ctx = this.ctx;
    const g = this.geometry();
    const { halfBins, frames } = this.result.params;
    ctx.clearRect(0, 0, g.cssW, g.cssH);
    ctx.fillStyle = '#0b0d12';
    ctx.fillRect(g.plotX, g.plotY, g.plotW, g.plotH);

    const cellW = g.plotW / frames;
    const cellH = g.plotH / halfBins;
    const off = typeof document !== 'undefined' ? document.createElement('canvas') : null;
    off.width = frames;
    off.height = halfBins;
    const octx = off.getContext ? off.getContext('2d') : null;
    if (octx) {
      const img = octx.createImageData(frames, halfBins);
      for (let m = 0; m < frames; m++) {
        for (let k = 0; k < halfBins; k++) {
          const v = this.image[m * halfBins + k];
          const [r, gg, b] = viridis(v);
          const p = (k * frames + m) * 4;
          img.data[p] = r; img.data[p + 1] = gg; img.data[p + 2] = b; img.data[p + 3] = 255;
        }
      }
      octx.putImageData(img, 0, 0);
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(off, g.plotX, g.plotY, g.plotW, g.plotH);
    }

    this._drawAxes(ctx, g);
    ctx.strokeStyle = '#c9d4e3';
    ctx.lineWidth = 1;
    ctx.strokeRect(g.plotX + 0.5, g.plotY + 0.5, g.plotW - 1, g.plotH - 1);
    ctx.fillStyle = '#e7edf5';
    ctx.font = '12px system-ui, sans-serif';
    ctx.fillText(this.title, g.plotX, g.plotY - 9);

    if (this.hover) this._drawHover(ctx, g, cellW, cellH);
  }

  _drawAxes(ctx, g) {
    const nyquist = this.result.params.sampleRate / 2;
    const duration = this.result.params.frames * this.result.params.hop / this.result.params.sampleRate;
    ctx.fillStyle = '#9fb0c3';
    ctx.font = '10px system-ui, sans-serif';
    ctx.strokeStyle = 'rgba(255,255,255,0.15)';
    const timeTicks = 6;
    for (let i = 0; i <= timeTicks; i++) {
      const t = (duration * i) / timeTicks;
      const x = this.timeToX(t, g);
      ctx.beginPath(); ctx.moveTo(x, g.plotY); ctx.lineTo(x, g.plotY + g.plotH); ctx.stroke();
      ctx.fillText(`${t.toFixed(2)}s`, x - 12, g.plotY + g.plotH + 15);
    }
    const freqTicks = 5;
    for (let i = 0; i <= freqTicks; i++) {
      const f = (nyquist * i) / freqTicks;
      const y = this.freqToY(f, g);
      ctx.beginPath(); ctx.moveTo(g.plotX, y); ctx.lineTo(g.plotX + g.plotW, y); ctx.stroke();
      ctx.fillText(`${Math.round(f)} Hz`, 6, y + 3);
    }
  }

  _drawHover(ctx, g, cellW, cellH) {
    const { frame, bin } = this.hover;
    const x = g.plotX + frame * cellW;
    const y = g.plotY + g.plotH - (bin + 1) * cellH;
    ctx.strokeStyle = '#ffd166';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(x + 0.5, y + 0.5, Math.max(1, cellW), Math.max(1, cellH));
  }

  cellAtEvent(evt) {
    if (!this.result) return null;
    const rect = this.canvas.getBoundingClientRect();
    const px = evt.clientX - rect.left;
    const py = evt.clientY - rect.top;
    const g = this.geometry();
    if (px < g.plotX || px > g.plotX + g.plotW || py < g.plotY || py > g.plotY + g.plotH) return null;
    const { frames, halfBins } = this.result.params;
    const frame = Math.min(frames - 1, Math.max(0, Math.floor(((px - g.plotX) / g.plotW) * frames)));
    const binFromBottom = Math.floor(((g.plotY + g.plotH - py) / g.plotH) * halfBins);
    const bin = Math.min(halfBins - 1, Math.max(0, binFromBottom));
    return { frame, bin, time: this.xToTime(px, g), freq: this.yToFreq(py, g) };
  }

  _bind() {
    this.canvas.addEventListener('mousemove', (evt) => {
      const cell = this.cellAtEvent(evt);
      this.hover = cell ? { frame: cell.frame, bin: cell.bin } : null;
      this.draw();
    });
    this.canvas.addEventListener('mouseleave', () => {
      this.hover = null;
      this.draw();
    });
    this.canvas.addEventListener('click', (evt) => {
      const cell = this.cellAtEvent(evt);
      if (cell && this.onClick) this.onClick(cell, this.gridKey);
    });
  }
}
