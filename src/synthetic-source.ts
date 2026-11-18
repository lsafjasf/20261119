export interface SyntheticSource {
  stream: MediaStream;
  expectedChannels: number;
  resolution: string;
  cleanup: () => Promise<void>;
}

function drawSyntheticFrame(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  startedAt: number,
): void {
  const elapsed = (performance.now() - startedAt) / 1000;
  const gradient = context.createLinearGradient(0, 0, width, height);
  gradient.addColorStop(0, `hsl(${Math.floor(elapsed * 24) % 360} 62% 19%)`);
  gradient.addColorStop(0.55, `hsl(${Math.floor(elapsed * 18 + 90) % 360} 57% 29%)`);
  gradient.addColorStop(1, "#071114");
  context.fillStyle = gradient;
  context.fillRect(0, 0, width, height);

  context.strokeStyle = "rgba(166, 242, 225, 0.19)";
  context.lineWidth = 1;
  for (let x = 0; x < width; x += 40) {
    context.beginPath();
    context.moveTo(x, 0);
    context.lineTo(x, height);
    context.stroke();
  }
  for (let y = 0; y < height; y += 40) {
    context.beginPath();
    context.moveTo(0, y);
    context.lineTo(width, y);
    context.stroke();
  }

  const orbitX = width * 0.5 + Math.cos(elapsed * 1.5) * width * 0.33;
  const orbitY = height * 0.5 + Math.sin(elapsed * 1.15) * height * 0.29;
  const glow = context.createRadialGradient(orbitX, orbitY, 2, orbitX, orbitY, height * 0.2);
  glow.addColorStop(0, "rgba(242, 185, 85, 0.95)");
  glow.addColorStop(1, "rgba(242, 185, 85, 0)");
  context.fillStyle = glow;
  context.fillRect(0, 0, width, height);
  context.fillStyle = "#f7c96b";
  context.beginPath();
  context.arc(orbitX, orbitY, Math.max(10, height * 0.035), 0, Math.PI * 2);
  context.fill();

  context.fillStyle = "rgba(247, 255, 252, 0.92)";
  context.font = `600 ${Math.round(width * 0.032)}px ui-monospace, monospace`;
  context.fillText("PROJECT SYNTHETIC FRAME", 22, 42);
  context.fillStyle = "rgba(158, 234, 217, 0.8)";
  context.font = `500 ${Math.round(width * 0.022)}px ui-monospace, monospace`;
  context.fillText(`t=${elapsed.toFixed(3)}s`, 22, 72);
}

export async function createSyntheticSource(): Promise<SyntheticSource> {
  const width = 640;
  const height = 360;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { alpha: false });
  if (!context) throw new Error("无法创建模拟画面的 2D 上下文。");

  const startedAt = performance.now();
  drawSyntheticFrame(context, width, height, startedAt);
  const timer = window.setInterval(() => {
    drawSyntheticFrame(context, width, height, startedAt);
  }, 1000 / 30);
  const videoStream = canvas.captureStream(30);

  const audioContext = new AudioContext();
  await audioContext.resume();
  const destination = audioContext.createMediaStreamDestination();
  destination.channelCount = 2;
  destination.channelCountMode = "explicit";
  const merger = audioContext.createChannelMerger(2);
  const left = audioContext.createOscillator();
  const right = audioContext.createOscillator();
  const gain = audioContext.createGain();
  left.type = "sine";
  right.type = "sine";
  left.frequency.value = 220;
  right.frequency.value = 330;
  gain.gain.value = 0.025;
  left.connect(merger, 0, 0);
  right.connect(merger, 0, 1);
  merger.connect(gain);
  gain.connect(destination);
  left.start();
  right.start();

  const stream = new MediaStream([
    ...videoStream.getVideoTracks(),
    ...destination.stream.getAudioTracks(),
  ]);
  return {
    stream,
    expectedChannels: 2,
    resolution: "640×360（模拟源）",
    cleanup: async () => {
      window.clearInterval(timer);
      for (const track of stream.getTracks()) {
        try {
          track.stop();
        } catch {
          // A video track transferred to the worker may already be detached.
        }
      }
      try {
        left.stop();
        right.stop();
        await audioContext.close();
      } catch {
        // AudioContext may already be closed by page teardown.
      }
    },
  };
}
