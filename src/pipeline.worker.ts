import type {
  CapabilityReport,
  PipelineConfig,
  PipelineMetrics,
  PipelineStopReport,
  WorkerInboundMessage,
  WorkerOutboundMessage,
} from "./types";

interface GeneratorHandle {
  track: MediaStreamTrack;
  writable: WritableStream<VideoFrame>;
}

interface PipelineState {
  id: number;
  sourceTrack: MediaStreamTrack;
  outputTrack: MediaStreamTrack;
  reader: ReadableStreamDefaultReader<VideoFrame>;
  writer: WritableStreamDefaultWriter<VideoFrame>;
  queue: VideoFrame[];
  wakeProcessor: (() => void) | null;
  currentFrame: VideoFrame | null;
  pauseController: AbortController | null;
  config: PipelineConfig;
  metrics: PipelineMetrics;
  closedFrames: number;
  stopped: boolean;
  metricsTimer: number;
}

interface BridgeState {
  config: PipelineConfig;
  metrics: PipelineMetrics;
  currentFrame: VideoFrame | null;
  pauseController: AbortController | null;
  canvas: OffscreenCanvas;
  closedFrames: number;
  stopped: boolean;
  metricsTimer: number;
}

const workerScope = self as unknown as DedicatedWorkerGlobalScope;
let activePipeline: PipelineState | null = null;
let activeBridge: BridgeState | null = null;
let nextPipelineId = 1;
let actionQueue = Promise.resolve();

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function normalizeConfig(config: Partial<PipelineConfig>): PipelineConfig {
  return {
    scale: clamp(Number(config.scale ?? 1), 0.25, 2),
    delayMs: clamp(Number(config.delayMs ?? 120), 0, 2000),
    maxQueue: Math.round(clamp(Number(config.maxQueue ?? 2), 1, 4)),
  };
}

function post(message: WorkerOutboundMessage, transfer: Transferable[] = []): void {
  workerScope.postMessage(message, transfer);
}

function capabilities(): CapabilityReport {
  const hasProcessor = typeof MediaStreamTrackProcessor !== "undefined";
  const hasVideoGenerator = typeof VideoTrackGenerator !== "undefined";
  const hasLegacyGenerator = typeof MediaStreamTrackGenerator !== "undefined";
  const missing: string[] = [];
  if (!hasProcessor) missing.push("MediaStreamTrackProcessor");
  if (!hasVideoGenerator && !hasLegacyGenerator) missing.push("VideoTrackGenerator");
  if (typeof VideoFrame === "undefined") missing.push("VideoFrame");
  if (typeof OffscreenCanvas === "undefined") missing.push("OffscreenCanvas");
  return {
    mediaStreamTrackProcessor: hasProcessor,
    videoTrackGenerator: hasVideoGenerator,
    mediaStreamTrackGenerator: hasLegacyGenerator,
    videoFrame: typeof VideoFrame !== "undefined",
    offscreenCanvas: typeof OffscreenCanvas !== "undefined",
    mediaRecorder: typeof MediaRecorder !== "undefined",
    supported: missing.length === 0,
    missing,
  };
}

function createGenerator(): GeneratorHandle {
  if (typeof VideoTrackGenerator !== "undefined") {
    const generator = new VideoTrackGenerator();
    return { track: generator.track, writable: generator.writable };
  }
  if (typeof MediaStreamTrackGenerator !== "undefined") {
    const generator = new MediaStreamTrackGenerator<VideoFrame>({ kind: "video" });
    return {
      track: generator as unknown as MediaStreamTrack,
      writable: generator.writable,
    };
  }
  throw new Error("当前浏览器没有可用的视频轨道生成器（VideoTrackGenerator）。");
}

function initialMetrics(config: PipelineConfig): PipelineMetrics {
  return {
    processed: 0,
    droppedBacklog: 0,
    discardedPaused: 0,
    errors: 0,
    queueLength: 0,
    maxQueue: config.maxQueue,
    lastSourceTimestamp: null,
    lastOutputTimestamp: null,
    timestampMatches: true,
    lastSourceWidth: 0,
    lastSourceHeight: 0,
    lastOutputWidth: 0,
    lastOutputHeight: 0,
    paused: false,
  };
}

function closeFrame(state: { closedFrames: number }, frame: VideoFrame | null): boolean {
  if (!frame) return false;
  try {
    frame.close();
    state.closedFrames += 1;
    return true;
  } catch {
    return false;
  }
}

function sendMetrics(state: PipelineState): void {
  if (state.stopped) return;
  state.metrics.queueLength = state.queue.length;
  post({ type: "metrics", metrics: { ...state.metrics } });
}

function signalProcessor(state: PipelineState): void {
  const wake = state.wakeProcessor;
  state.wakeProcessor = null;
  wake?.();
}

function enqueueFrame(state: PipelineState, frame: VideoFrame): void {
  while (state.queue.length >= state.config.maxQueue) {
    const oldest = state.queue.shift();
    if (closeFrame(state, oldest ?? null)) {
      state.metrics.droppedBacklog += 1;
    }
  }
  state.queue.push(frame);
  state.metrics.queueLength = state.queue.length;
  signalProcessor(state);
}

function waitForFrame(state: PipelineState): Promise<void> {
  if (state.queue.length > 0 || state.stopped) return Promise.resolve();
  return new Promise((resolve) => {
    state.wakeProcessor = resolve;
  });
}

function abortableDelay(
  state: { pauseController: AbortController | null; stopped: boolean },
  delayMs: number,
): Promise<void> {
  if (delayMs <= 0 || state.stopped) return Promise.resolve();
  return new Promise((resolve) => {
    const controller = new AbortController();
    state.pauseController = controller;
    const finish = (): void => {
      clearTimeout(timer);
      controller.signal.removeEventListener("abort", finish);
      if (state.pauseController === controller) state.pauseController = null;
      resolve();
    };
    const timer = setTimeout(finish, delayMs);
    controller.signal.addEventListener("abort", finish, { once: true });
  });
}

async function readFrames(state: PipelineState): Promise<void> {
  try {
    while (!state.stopped) {
      const result = await state.reader.read();
      if (result.done) break;
      const frame = result.value;
      if (state.stopped) {
        closeFrame(state, frame);
        break;
      }
      if (state.metrics.paused) {
        if (closeFrame(state, frame)) state.metrics.discardedPaused += 1;
        continue;
      }
      enqueueFrame(state, frame);
    }
  } catch (error) {
    if (!state.stopped) {
      post({
        type: "error",
        code: "pipeline-failed",
        message: `读取视频帧失败：${error instanceof Error ? error.message : String(error)}`,
      });
      await stopPipeline(state, "pipeline-failed");
      return;
    }
  }

  if (!state.stopped) {
    post({ type: "error", code: "source-ended", message: "视频源已结束。" });
    await stopPipeline(state, "source-ended");
  }
}

function evenDimension(value: number): number {
  const rounded = Math.max(2, Math.round(value));
  return rounded % 2 === 0 ? rounded : rounded - 1;
}

function sourceTimestampLabel(timestampUs: number): string {
  const totalMs = Math.floor(timestampUs / 1000);
  const hours = Math.floor(totalMs / 3_600_000);
  const minutes = Math.floor((totalMs % 3_600_000) / 60_000);
  const seconds = Math.floor((totalMs % 60_000) / 1000);
  const millis = totalMs % 1000;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(
    seconds,
  ).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

function renderFrame(
  frame: VideoFrame,
  canvas: OffscreenCanvas,
  config: PipelineConfig,
): VideoFrame {
  const sourceWidth = frame.displayWidth;
  const sourceHeight = frame.displayHeight;
  const width = evenDimension(sourceWidth * config.scale);
  const height = evenDimension(sourceHeight * config.scale);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const context = canvas.getContext("2d", { alpha: false });
  if (!context) throw new Error("无法创建 OffscreenCanvas 2D 上下文。");

  context.drawImage(frame as unknown as CanvasImageSource, 0, 0, width, height);

  const padding = Math.max(8, Math.round(width * 0.018));
  const fontSize = Math.max(13, Math.round(width * 0.026));
  const smallSize = Math.max(10, Math.round(fontSize * 0.68));
  const lineOne = `SRC ${sourceTimestampLabel(frame.timestamp)}`;
  const lineTwo = `${new Date().toLocaleString("zh-CN", { hour12: false })}  ${width}x${height}`;
  context.font = `600 ${fontSize}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  const boxWidth = Math.max(context.measureText(lineOne).width, context.measureText(lineTwo).width);
  const boxHeight = fontSize + smallSize + padding * 2.3;
  const x = padding;
  const y = height - boxHeight - padding;

  context.fillStyle = "rgba(4, 15, 18, 0.78)";
  context.fillRect(x, y, boxWidth + padding * 2, boxHeight);
  context.fillStyle = "rgba(99, 223, 195, 0.9)";
  context.fillRect(x, y, Math.max(3, Math.round(fontSize * 0.16)), boxHeight);
  context.textBaseline = "top";
  context.fillStyle = "#f3fffc";
  context.fillText(lineOne, x + padding, y + padding * 0.7);
  context.font = `500 ${smallSize}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  context.fillStyle = "#9eead9";
  context.fillText(lineTwo, x + padding, y + padding * 0.7 + fontSize * 1.18);

  const init: VideoFrameInit = {
    timestamp: frame.timestamp,
    alpha: "discard",
  };
  if (frame.duration !== null && frame.duration > 0) init.duration = frame.duration;
  return new VideoFrame(canvas, init);
}

async function processFrames(state: PipelineState): Promise<void> {
  const canvas = new OffscreenCanvas(16, 16);
  while (!state.stopped) {
    await waitForFrame(state);
    if (state.stopped) break;
    const frame = state.queue.shift();
    if (!frame) continue;

    state.currentFrame = frame;
    state.metrics.queueLength = state.queue.length;
    await abortableDelay(state, state.config.delayMs);
    if (state.stopped || state.metrics.paused) {
      closeFrame(state, frame);
      state.currentFrame = null;
      continue;
    }

    let outputFrame: VideoFrame | null = null;
    try {
      outputFrame = renderFrame(frame, canvas, state.config);
      state.metrics.lastSourceTimestamp = frame.timestamp;
      state.metrics.lastOutputTimestamp = outputFrame.timestamp;
      state.metrics.timestampMatches = frame.timestamp === outputFrame.timestamp;
      state.metrics.lastSourceWidth = frame.displayWidth;
      state.metrics.lastSourceHeight = frame.displayHeight;
      state.metrics.lastOutputWidth = outputFrame.displayWidth;
      state.metrics.lastOutputHeight = outputFrame.displayHeight;
      await state.writer.write(outputFrame);
      state.metrics.processed += 1;
    } catch (error) {
      state.metrics.errors += 1;
      if (!state.stopped) {
        post({
          type: "error",
          code: "pipeline-failed",
          message: `处理视频帧失败：${error instanceof Error ? error.message : String(error)}`,
        });
      }
    } finally {
      closeFrame(state, outputFrame);
      closeFrame(state, frame);
      state.currentFrame = null;
    }
  }
}

function makeStopReport(
  state: PipelineState,
  overrides: Partial<PipelineStopReport>,
): PipelineStopReport {
  return {
    sourceStopped: false,
    outputStopped: false,
    framesClosed: 0,
    queueClosed: 0,
    pendingFrameClosed: false,
    readerCancelled: false,
    writerClosed: false,
    metrics: { ...state.metrics },
    ...overrides,
  };
}

async function stopPipeline(
  state: PipelineState,
  reason: string,
): Promise<PipelineStopReport> {
  if (state.stopped && activePipeline !== state) {
    return makeStopReport(state, {});
  }
  state.stopped = true;
  state.pauseController?.abort();
  state.pauseController = null;
  signalProcessor(state);
  clearInterval(state.metricsTimer);

  let queueClosed = 0;
  for (const frame of state.queue.splice(0)) {
    if (closeFrame(state, frame)) queueClosed += 1;
  }
  const pendingFrameClosed = closeFrame(state, state.currentFrame);
  state.currentFrame = null;

  let readerCancelled = false;
  try {
    await state.reader.cancel(reason);
    readerCancelled = true;
  } catch {
    readerCancelled = false;
  }

  let writerClosed = false;
  try {
    await state.writer.close();
    writerClosed = true;
  } catch {
    writerClosed = false;
  }

  let sourceStopped = false;
  try {
    state.sourceTrack.stop();
    sourceStopped = state.sourceTrack.readyState === "ended";
  } catch {
    sourceStopped = false;
  }

  let outputStopped = false;
  try {
    state.outputTrack.stop();
    outputStopped = state.outputTrack.readyState === "ended";
  } catch {
    outputStopped = false;
  }

  const report = makeStopReport(state, {
    sourceStopped,
    outputStopped,
    framesClosed: state.closedFrames,
    queueClosed,
    pendingFrameClosed,
    readerCancelled,
    writerClosed,
  });
  if (activePipeline === state) activePipeline = null;
  post({ type: "stopped", report });
  return report;
}

async function startPipeline(message: Extract<WorkerInboundMessage, { type: "start" }>): Promise<void> {
  await stopActivePipeline("restart");
  await stopBridge();
  const caps = capabilities();
  if (!caps.supported) {
    post({
      type: "error",
      code: "api-unavailable",
      message: `当前浏览器缺少必要接口：${caps.missing.join("、")}`,
    });
    return;
  }

  const config = normalizeConfig(message.config);
  const generator = createGenerator();
  const processor = new MediaStreamTrackProcessor({
    track: message.track,
    maxBufferSize: 2,
  });
  const state: PipelineState = {
    id: nextPipelineId,
    sourceTrack: message.track,
    outputTrack: generator.track,
    reader: processor.readable.getReader(),
    writer: generator.writable.getWriter(),
    queue: [],
    wakeProcessor: null,
    currentFrame: null,
    pauseController: null,
    config,
    metrics: initialMetrics(config),
    closedFrames: 0,
    stopped: false,
    metricsTimer: 0,
  };
  nextPipelineId += 1;
  activePipeline = state;

  post(
    { type: "ready", track: generator.track, config: { ...config } },
    [generator.track as unknown as Transferable],
  );
  state.metricsTimer = workerScope.setInterval(() => sendMetrics(state), 250);
  void readFrames(state);
  void processFrames(state);
}

async function stopActivePipeline(reason: string): Promise<void> {
  const state = activePipeline;
  if (!state) return;
  await stopPipeline(state, reason);
}

function sendBridgeMetrics(state: BridgeState): void {
  if (state.stopped) return;
  post({ type: "metrics", metrics: { ...state.metrics } });
}

async function stopBridge(): Promise<void> {
  const state = activeBridge;
  if (!state) return;
  state.stopped = true;
  state.pauseController?.abort();
  state.pauseController = null;
  clearInterval(state.metricsTimer);
  closeFrame(state, state.currentFrame);
  state.currentFrame = null;
  const report: PipelineStopReport = {
    sourceStopped: false,
    outputStopped: false,
    framesClosed: state.closedFrames,
    queueClosed: 0,
    pendingFrameClosed: false,
    readerCancelled: false,
    writerClosed: false,
    metrics: { ...state.metrics },
  };
  activeBridge = null;
  post({ type: "stopped", report });
}

function startBridge(config: PipelineConfig): void {
  const state: BridgeState = {
    config,
    metrics: initialMetrics(config),
    currentFrame: null,
    pauseController: null,
    canvas: new OffscreenCanvas(16, 16),
    closedFrames: 0,
    stopped: false,
    metricsTimer: 0,
  };
  activeBridge = state;
  post({ type: "bridge-ready", config: { ...config } });
  state.metricsTimer = workerScope.setInterval(() => sendBridgeMetrics(state), 250);
}

async function handleBridgeFrame(sequence: number, frame: VideoFrame): Promise<void> {
  const state = activeBridge;
  if (!state || state.stopped) {
    frame.close();
    return;
  }
  state.currentFrame = frame;
  await abortableDelay(state, state.config.delayMs);
  if (state.stopped) {
    closeFrame(state, frame);
    state.currentFrame = null;
    return;
  }
  if (state.metrics.paused) {
    closeFrame(state, frame);
    state.metrics.discardedPaused += 1;
    state.currentFrame = null;
    post({
      type: "bridge-frame-done",
      sequence,
      frame: null,
      metrics: { ...state.metrics },
    });
    return;
  }

  let output: VideoFrame | null = null;
  try {
    output = renderFrame(frame, state.canvas, state.config);
    state.metrics.lastSourceTimestamp = frame.timestamp;
    state.metrics.lastOutputTimestamp = output.timestamp;
    state.metrics.timestampMatches = frame.timestamp === output.timestamp;
    state.metrics.lastSourceWidth = frame.displayWidth;
    state.metrics.lastSourceHeight = frame.displayHeight;
    state.metrics.lastOutputWidth = output.displayWidth;
    state.metrics.lastOutputHeight = output.displayHeight;
    state.metrics.processed += 1;
    post(
      {
        type: "bridge-frame-done",
        sequence,
        frame: output,
        metrics: { ...state.metrics },
      },
      [output as unknown as Transferable],
    );
    output = null;
  } catch (error) {
    state.metrics.errors += 1;
    post({
      type: "error",
      code: "pipeline-failed",
      message: `Worker 处理帧失败：${error instanceof Error ? error.message : String(error)}`,
    });
    post({
      type: "bridge-frame-done",
      sequence,
      frame: null,
      metrics: { ...state.metrics },
    });
  } finally {
    closeFrame(state, output);
    closeFrame(state, frame);
    state.currentFrame = null;
  }
}

async function handleMessage(message: WorkerInboundMessage): Promise<void> {
  switch (message.type) {
    case "probe":
      post({ type: "capabilities", capabilities: capabilities() });
      break;
    case "start":
      await startPipeline(message);
      break;
    case "start-bridge": {
      await stopActivePipeline("restart-bridge");
      await stopBridge();
      if (typeof VideoFrame === "undefined" || typeof OffscreenCanvas === "undefined") {
        post({
          type: "error",
          code: "api-unavailable",
          message: "兼容桥接缺少 VideoFrame 或 OffscreenCanvas，无法在 Worker 内处理帧。",
        });
        break;
      }
      startBridge(normalizeConfig(message.config));
      break;
    }
    case "bridge-frame":
      break;
    case "configure": {
      if (activePipeline) {
        const merged = normalizeConfig({ ...activePipeline.config, ...message.config });
        activePipeline.config = merged;
        activePipeline.metrics.maxQueue = merged.maxQueue;
        while (activePipeline.queue.length > merged.maxQueue) {
          const dropped = activePipeline.queue.shift();
          if (closeFrame(activePipeline, dropped ?? null)) {
            activePipeline.metrics.droppedBacklog += 1;
          }
        }
        sendMetrics(activePipeline);
      }
      if (activeBridge) {
        activeBridge.config = normalizeConfig({ ...activeBridge.config, ...message.config });
        activeBridge.metrics.maxQueue = activeBridge.config.maxQueue;
        sendBridgeMetrics(activeBridge);
      }
      break;
    }
    case "pause": {
      if (activePipeline) {
        activePipeline.metrics.paused = true;
        activePipeline.pauseController?.abort();
        for (const frame of activePipeline.queue.splice(0)) {
          if (closeFrame(activePipeline, frame)) activePipeline.metrics.discardedPaused += 1;
        }
        sendMetrics(activePipeline);
      }
      if (activeBridge) {
        activeBridge.metrics.paused = true;
        activeBridge.pauseController?.abort();
        sendBridgeMetrics(activeBridge);
      }
      break;
    }
    case "resume": {
      if (activePipeline) {
        activePipeline.metrics.paused = false;
        signalProcessor(activePipeline);
        sendMetrics(activePipeline);
      }
      if (activeBridge) {
        activeBridge.metrics.paused = false;
        sendBridgeMetrics(activeBridge);
      }
      break;
    }
    case "stop":
      await stopActivePipeline("user-stop");
      await stopBridge();
      break;
  }
}

workerScope.onmessage = (event: MessageEvent<WorkerInboundMessage>) => {
  if (event.data.type === "bridge-frame") {
    void handleBridgeFrame(event.data.sequence, event.data.frame).catch((error: unknown) => {
      closeFrame({ closedFrames: 0 }, event.data.type === "bridge-frame" ? event.data.frame : null);
      post({
        type: "error",
        code: "pipeline-failed",
        message: `Worker 桥接帧处理异常：${error instanceof Error ? error.message : String(error)}`,
      });
    });
    return;
  }
  actionQueue = actionQueue
    .then(() => handleMessage(event.data))
    .catch((error: unknown) => {
      post({
        type: "error",
        code: "pipeline-failed",
        message: `Worker 管线异常：${error instanceof Error ? error.message : String(error)}`,
      });
    });
};
