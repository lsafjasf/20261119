function roundedRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + width, y, x + width, y + height, r);
  ctx.arcTo(x + width, y + height, x, y + height, r);
  ctx.arcTo(x, y + height, x, y, r);
  ctx.arcTo(x, y, x + width, y, r);
  ctx.closePath();
}

export class GrainView {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.status = null;
    this.receivedAt = 0;
    this.lastRejected = 0;
    this.rejectFlashUntil = 0;
    this.lastAccepted = 0;
    this.triggerFlashUntil = 0;
  }

  update(status) {
    const now = performance.now();
    if (status.rejected > this.lastRejected) this.rejectFlashUntil = now + 320;
    if (status.accepted > this.lastAccepted) this.triggerFlashUntil = now + 190;
    this.lastRejected = status.rejected;
    this.lastAccepted = status.accepted;
    this.status = status;
    this.receivedAt = now;
  }

  resize() {
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    const width = Math.max(1, Math.round(this.canvas.clientWidth * ratio));
    const height = Math.max(1, Math.round(this.canvas.clientHeight * ratio));
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
    }
    return { width, height, ratio };
  }

  draw(sampleRate) {
    const { width, height, ratio } = this.resize();
    const ctx = this.ctx;
    const status = this.status;
    ctx.clearRect(0, 0, width, height);
    if (!status || !status.sourceDurationSeconds) return;

    const now = performance.now();
    const paddingLeft = 27 * ratio;
    const paddingRight = 12 * ratio;
    const top = 26 * ratio;
    const bottom = 22 * ratio;
    const plotWidth = width - paddingLeft - paddingRight;
    const plotHeight = height - top - bottom;
    const laneCount = Math.max(4, Math.min(status.maxVoices, Math.floor(plotHeight / (15 * ratio))));
    const laneHeight = plotHeight / laneCount;
    const duration = status.sourceDurationSeconds;
    const frozenColor = "#f0b666";
    const activeColor = status.frozen ? frozenColor : "#b6f36b";

    ctx.lineWidth = ratio;
    for (let lane = 0; lane < laneCount; lane += 1) {
      const y = top + lane * laneHeight;
      ctx.strokeStyle = lane % 2 === 0
        ? "rgba(208, 232, 216, 0.045)"
        : "rgba(208, 232, 216, 0.022)";
      ctx.beginPath();
      ctx.moveTo(paddingLeft, y);
      ctx.lineTo(width - paddingRight, y);
      ctx.stroke();
    }

    ctx.fillStyle = "rgba(127, 145, 133, 0.56)";
    ctx.font = `${7.5 * ratio}px "SFMono-Regular", Consolas, monospace`;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let lane = 0; lane < laneCount; lane += 3) {
      ctx.fillText(String(lane + 1).padStart(2, "0"), paddingLeft - 7 * ratio, top + (lane + 0.5) * laneHeight);
    }

    const xOfSource = (seconds) => paddingLeft
      + Math.min(1, Math.max(0, seconds / Math.max(duration, 1e-9))) * plotWidth;

    const headX = xOfSource(status.readPositionSeconds);
    ctx.strokeStyle = status.frozen ? "rgba(240, 182, 102, 0.65)" : "rgba(182, 243, 107, 0.35)";
    ctx.setLineDash([3 * ratio, 4 * ratio]);
    ctx.beginPath();
    ctx.moveTo(headX, top - 4 * ratio);
    ctx.lineTo(headX, height - bottom + 2 * ratio);
    ctx.stroke();
    ctx.setLineDash([]);

    const elapsedFrames = (now - this.receivedAt) / 1000 * sampleRate;
    const grainHeight = Math.max(4 * ratio, Math.min(9 * ratio, laneHeight * 0.58));
    for (const grain of status.grains) {
      const grainWidthSeconds = grain.length / sampleRate;
      const grainWidth = Math.max(3 * ratio, grainWidthSeconds / duration * plotWidth);
      const age = Math.min(grain.length, grain.age + elapsedFrames);
      const progress = Math.min(1, age / grain.length);
      const lane = (grain.id - 1) % laneCount;
      const y = top + lane * laneHeight + (laneHeight - grainHeight) / 2;
      let startX = xOfSource(grain.sourceStartSeconds);
      let segmentWidth = grainWidth;

      if (grain.sourceStartSeconds + grainWidthSeconds > duration) {
        segmentWidth = Math.max(2 * ratio, xOfSource(duration) - startX);
      }

      ctx.globalAlpha = 0.72 + progress * 0.28;
      roundedRect(ctx, startX, y, segmentWidth, grainHeight, grainHeight / 2);
      ctx.fillStyle = status.frozen ? "rgba(240, 182, 102, 0.12)" : "rgba(182, 243, 107, 0.1)";
      ctx.fill();
      ctx.strokeStyle = status.frozen ? "rgba(240, 182, 102, 0.48)" : "rgba(182, 243, 107, 0.44)";
      ctx.stroke();

      const fillWidth = Math.max(1 * ratio, segmentWidth * progress);
      roundedRect(ctx, startX, y, fillWidth, grainHeight, grainHeight / 2);
      ctx.fillStyle = activeColor;
      ctx.fill();
      ctx.globalAlpha = 1;

      if (segmentWidth > 36 * ratio) {
        ctx.fillStyle = "rgba(5, 10, 7, 0.86)";
        ctx.font = `${7 * ratio}px "SFMono-Regular", Consolas, monospace`;
        ctx.textAlign = "left";
        ctx.fillText(
          `${grain.sourceStartSeconds.toFixed(3)}s`,
          startX + 5 * ratio,
          y + grainHeight / 2
        );
      }
    }

    if (now < this.triggerFlashUntil || now < this.rejectFlashUntil) {
      const rejected = now < this.rejectFlashUntil;
      ctx.fillStyle = rejected ? "rgba(255, 129, 120, 0.09)" : "rgba(182, 243, 107, 0.055)";
      ctx.fillRect(paddingLeft, top, plotWidth, plotHeight);
    }

    ctx.fillStyle = status.frozen ? "#f0b666" : "#b6f36b";
    ctx.beginPath();
    ctx.arc(headX, top - 7 * ratio, 2.7 * ratio, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = "rgba(127, 145, 133, 0.68)";
    ctx.font = `${7.5 * ratio}px "SFMono-Regular", Consolas, monospace`;
    ctx.textBaseline = "alphabetic";
    ctx.textAlign = "left";
    ctx.fillText("0.000", paddingLeft, height - 6 * ratio);
    ctx.textAlign = "right";
    ctx.fillText(`${duration.toFixed(3)} s`, width - paddingRight, height - 6 * ratio);

    if (status.frozen) {
      ctx.fillStyle = "rgba(240, 182, 102, 0.9)";
      ctx.textAlign = "left";
      ctx.fillText("FROZEN", Math.min(headX + 5 * ratio, width - paddingRight - 39 * ratio), top - 6 * ratio);
    }
  }
}
