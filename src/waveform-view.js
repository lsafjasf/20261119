import { peakEnvelope } from "./dsp-utils.js";

export class WaveformView {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.source = new Float32Array(0);
    this.sourceDuration = 0;
    this.position = 0;
    this.frozen = false;
    this.peaks = new Float32Array(0);
    this.peaksWidth = 0;
  }

  setSource(source, duration) {
    this.source = source;
    this.sourceDuration = duration;
    this.peaks = new Float32Array(0);
    this.peaksWidth = 0;
  }

  setPosition(seconds, frozen = false) {
    this.position = seconds;
    this.frozen = frozen;
  }

  resize() {
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    const width = Math.max(1, Math.round(this.canvas.clientWidth * ratio));
    const height = Math.max(1, Math.round(this.canvas.clientHeight * ratio));
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
      this.peaks = new Float32Array(0);
    }
    return { width, height, ratio };
  }

  draw() {
    const { width, height, ratio } = this.resize();
    const ctx = this.ctx;
    ctx.clearRect(0, 0, width, height);

    const paddingX = 12 * ratio;
    const top = 15 * ratio;
    const bottom = 18 * ratio;
    const plotWidth = width - paddingX * 2;
    const plotHeight = height - top - bottom;
    const centerY = top + plotHeight / 2;
    const buckets = Math.max(1, Math.floor(plotWidth / (2.1 * ratio)));

    if (this.peaks.length !== buckets && this.source.length) {
      this.peaks = peakEnvelope(this.source, buckets);
      this.peaksWidth = plotWidth;
    }

    ctx.strokeStyle = "rgba(208, 232, 216, 0.08)";
    ctx.lineWidth = ratio;
    ctx.beginPath();
    ctx.moveTo(paddingX, centerY);
    ctx.lineTo(width - paddingX, centerY);
    ctx.stroke();

    if (this.peaks.length) {
      const barWidth = plotWidth / this.peaks.length;
      const gradient = ctx.createLinearGradient(0, centerY - plotHeight * 0.5, 0, centerY + plotHeight * 0.5);
      gradient.addColorStop(0, "rgba(182, 243, 107, 0.78)");
      gradient.addColorStop(0.5, "rgba(138, 201, 84, 0.95)");
      gradient.addColorStop(1, "rgba(182, 243, 107, 0.52)");
      ctx.fillStyle = gradient;
      for (let i = 0; i < this.peaks.length; i += 1) {
        const amplitude = Math.max(0.7 * ratio, this.peaks[i] * plotHeight * 0.48);
        const x = paddingX + i * barWidth;
        ctx.fillRect(x, centerY - amplitude, Math.max(1, barWidth * 0.68), amplitude * 2);
      }
    }

    const duration = Math.max(this.sourceDuration, 1e-9);
    const x = paddingX + Math.min(1, Math.max(0, this.position / duration)) * plotWidth;
    ctx.strokeStyle = this.frozen ? "#f0b666" : "#b6f36b";
    ctx.lineWidth = Math.max(1, 1.2 * ratio);
    ctx.beginPath();
    ctx.moveTo(x, top - 3 * ratio);
    ctx.lineTo(x, height - bottom + 4 * ratio);
    ctx.stroke();

    ctx.fillStyle = this.frozen ? "#f0b666" : "#b6f36b";
    ctx.beginPath();
    ctx.arc(x, top - 4 * ratio, 3.3 * ratio, 0, Math.PI * 2);
    ctx.fill();

    if (this.frozen) {
      ctx.fillStyle = "rgba(240, 182, 102, 0.08)";
      const frozenWidth = Math.max(18 * ratio, plotWidth * 0.012);
      ctx.fillRect(x - frozenWidth / 2, top, frozenWidth, plotHeight);
    }
  }

  positionFromPointer(event) {
    const rect = this.canvas.getBoundingClientRect();
    const ratio = this.canvas.width / Math.max(1, rect.width);
    const paddingX = 12 * ratio;
    const plotWidth = this.canvas.width - paddingX * 2;
    const local = (event.clientX - rect.left) * ratio;
    return Math.min(1, Math.max(0, (local - paddingX) / Math.max(1, plotWidth)));
  }
}
