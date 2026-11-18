import type {
  PipelineConfig,
  PipelineMetrics,
  PipelineStopReport,
  WorkerInboundMessage,
  WorkerOutboundMessage,
} from "./types";

type BridgeErrorHandler = (message: string, code: string) => void;
type BridgeMetricsHandler = (metrics: PipelineMetrics) => void;

interface PendingFrame {
  sequence: number;
  resolve: (frame: VideoFrame | null) => void;
  reject: (error: Error) => void;
}

function generatorHandle(): {
  track: MediaStreamTrack;
  writable: WritableStream<VideoFrame>;
} {
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
  throw new Error(
    "主线程也没有 VideoTrackGenerator 或 MediaStreamTrackGenerator，无法生成输出轨道。",
  );
}

export function canUseBridgeMode(): boolean {
  return (
    typeof MediaStreamTrackProcessor !== "undefined" &&
    (typeof VideoTrackGenerator !== "undefined" ||
      typeof MediaStreamTrackGenerator !== "undefined")
  );
}

export class BridgePipelineClient {
  private readonly worker: Worker;
  private processor: MediaStreamTrackProcessor | null = null;
  private reader: ReadableStreamDefaultReader<VideoFrame> | null = null;
  private writer: WritableStreamDefaultWriter<VideoFrame> | null = null;
  private outputTrack: MediaStreamTrack | null = null;
  private sourceTrack: MediaStreamTrack | null = null;
  private pendingFrame: PendingFrame | null = null;
  private readyResolve: (() => void) | null = null;
  private readyReject: ((error: Error) => void) | null = null;
  private stopResolve: ((report: PipelineStopReport) => void) | null = null;
  private stopReject: ((error: Error) => void) | null = null;
  private lastWorkerReport: PipelineStopReport | null = null;
  private lastWorkerMetrics: PipelineMetrics | null = null;
  private lastMetrics: PipelineMetrics | null = null;
  private paused = false;
  private stopped = false;
  private pumping = false;
  private sequence = 0;
  private mainFramesClosed = 0;
  private pausedDiscarded = 0;
  private metricsTimer = 0;
  onError: BridgeErrorHandler | null = null;
  onMetrics: BridgeMetricsHandler | null = null;

  constructor() {
    this.worker = new Worker(new URL("./pipeline.worker.ts", import.meta.url), {
      type: "module",
      name: "timestamp-video-bridge",
    });
    this.worker.onmessage = (event: MessageEvent<WorkerOutboundMessage>) => {
      this.handleMessage(event.data);
    };
    this.worker.onerror = (event) => {
      this.fail(new Error(event.message || "Worker 桥接管线启动失败。"));
    };
  }

  private fail(error: Error): void {
    this.readyReject?.(error);
    this.stopReject?.(error);
    this.pendingFrame?.reject(error);
    this.readyResolve = null;
    this.pendingFrame = null;
    this.onError?.(error.message, "worker-crash");
  }

  private post(message: WorkerInboundMessage, transfer: Transferable[] = []): void {
    this.worker.postMessage(message, transfer);
  }

  private collectMetrics(workerMetrics: PipelineMetrics): PipelineMetrics {
    const stats = this.processor as
      | (MediaStreamTrackProcessor & { discardedFrames?: number })
      | null;
    const discarded = stats?.discardedFrames ?? 0;
    return {
      ...workerMetrics,
      droppedBacklog: workerMetrics.droppedBacklog + discarded + this.pausedDiscarded,
      queueLength: 0,
      paused: this.paused,
    };
  }

  private handleMessage(message: WorkerOutboundMessage): void {
    switch (message.type) {
      case "bridge-ready":
        this.readyResolve?.();
        this.readyResolve = null;
        this.readyReject = null;
        break;
      case "bridge-frame-done": {
        this.lastWorkerMetrics = message.metrics;
        this.lastMetrics = this.collectMetrics(message.metrics);
        this.onMetrics?.(this.lastMetrics);
        const pending = this.pendingFrame;
        if (pending && pending.sequence === message.sequence) {
          this.pendingFrame = null;
          if (this.stopped && message.frame) {
            message.frame.close();
            pending.resolve(null);
          } else {
            pending.resolve(message.frame);
          }
        } else if (message.frame) {
          message.frame.close();
        }
        break;
      }
      case "metrics":
        this.lastWorkerMetrics = message.metrics;
        this.lastMetrics = this.collectMetrics(message.metrics);
        this.onMetrics?.(this.lastMetrics);
        break;
      case "stopped":
        this.lastWorkerReport = message.report;
        this.stopResolve?.(message.report);
        this.stopResolve = null;
        this.stopReject = null;
        break;
      case "error":
        if (message.code === "source-ended") return;
        this.onError?.(message.message, message.code);
        this.readyReject?.(new Error(message.message));
        this.pendingFrame?.reject(new Error(message.message));
        this.pendingFrame = null;
        break;
      case "ready":
      case "capabilities":
        break;
    }
  }

  async start(sourceTrack: MediaStreamTrack, config: PipelineConfig): Promise<MediaStreamTrack> {
    if (!canUseBridgeMode()) {
      throw new Error(
        "当前浏览器既没有 Worker 内帧接口，也没有主线程 MediaStreamTrackProcessor 与轨道生成器。",
      );
    }
    this.sourceTrack = sourceTrack;
    const generator = generatorHandle();
    this.outputTrack = generator.track;
    this.writer = generator.writable.getWriter();
    this.processor = new MediaStreamTrackProcessor({
      track: sourceTrack,
      maxBufferSize: 2,
    });
    this.reader = (this.processor.readable as ReadableStream<VideoFrame>).getReader();

    const ready = new Promise<void>((resolve, reject) => {
      this.readyResolve = resolve;
      this.readyReject = reject;
      window.setTimeout(() => {
        if (!this.readyResolve) return;
        this.readyResolve = null;
        this.readyReject = null;
        reject(new Error("Worker 兼容桥接启动超时。"));
      }, 8_000);
    });
    this.post({ type: "start-bridge", config });
    await ready;
    this.metricsTimer = window.setInterval(() => {
      if (this.lastWorkerMetrics) {
        this.onMetrics?.(this.collectMetrics(this.lastWorkerMetrics));
      }
    }, 250);
    void this.pumpFrames();
    return generator.track;
  }

  private async pumpFrames(): Promise<void> {
    if (this.pumping) return;
    this.pumping = true;
    const reader = this.reader;
    const writer = this.writer;
    if (!reader || !writer) return;
    try {
      while (!this.stopped) {
        const result = await reader.read();
        if (result.done) break;
        const frame = result.value;
        if (this.stopped) {
          frame.close();
          this.mainFramesClosed += 1;
          break;
        }
        if (this.paused) {
          frame.close();
          this.mainFramesClosed += 1;
          this.pausedDiscarded += 1;
          continue;
        }
        const output = await this.sendFrame(frame);
        if (!output) continue;
        if (this.stopped) {
          output.close();
          this.mainFramesClosed += 1;
          break;
        }
        try {
          await writer.write(output);
        } finally {
          output.close();
          this.mainFramesClosed += 1;
        }
      }
    } catch (error) {
      if (!this.stopped) {
        this.onError?.(
          `兼容桥接帧循环失败：${error instanceof Error ? error.message : String(error)}`,
          "pipeline-failed",
        );
      }
    } finally {
      this.pumping = false;
    }
  }

  private sendFrame(frame: VideoFrame): Promise<VideoFrame | null> {
    if (this.stopped) {
      frame.close();
      this.mainFramesClosed += 1;
      return Promise.resolve(null);
    }
    const sequence = this.sequence;
    this.sequence += 1;
    return new Promise((resolve, reject) => {
      this.pendingFrame = { sequence, resolve, reject };
      this.post({ type: "bridge-frame", sequence, frame }, [
        frame as unknown as Transferable,
      ]);
    });
  }

  configure(config: Partial<PipelineConfig>): void {
    if (this.stopped) return;
    this.post({ type: "configure", config });
  }

  pause(): void {
    if (this.stopped) return;
    this.paused = true;
    this.post({ type: "pause" });
  }

  resume(): void {
    if (this.stopped) return;
    this.paused = false;
    this.post({ type: "resume" });
  }

  async stop(): Promise<PipelineStopReport | null> {
    if (this.stopped) return this.lastWorkerReport;
    this.stopped = true;
    this.paused = false;
    clearInterval(this.metricsTimer);
    const stopped = new Promise<PipelineStopReport>((resolve, reject) => {
      this.stopResolve = resolve;
      this.stopReject = reject;
      window.setTimeout(() => reject(new Error("Worker 兼容桥接停止超时。")), 5_000);
    });
    this.post({ type: "stop" });
    let report: PipelineStopReport | null = null;
    try {
      report = await stopped;
    } catch {
      report = this.lastWorkerReport;
    }

    let readerCancelled = false;
    try {
      await this.reader?.cancel("user-stop");
      readerCancelled = true;
    } catch {
      readerCancelled = false;
    }
    let writerClosed = false;
    try {
      await this.writer?.close();
      writerClosed = true;
    } catch {
      writerClosed = false;
    }
    let sourceStopped = false;
    try {
      this.sourceTrack?.stop();
      sourceStopped = this.sourceTrack?.readyState === "ended";
    } catch {
      sourceStopped = false;
    }
    let outputStopped = false;
    try {
      this.outputTrack?.stop();
      outputStopped = this.outputTrack?.readyState === "ended";
    } catch {
      outputStopped = false;
    }
    this.pendingFrame?.resolve(null);
    this.pendingFrame = null;
    this.worker.terminate();

    if (!report) {
      report = {
        sourceStopped,
        outputStopped,
        framesClosed: this.mainFramesClosed,
        queueClosed: 0,
        pendingFrameClosed: false,
        readerCancelled,
        writerClosed,
        metrics:
          this.lastWorkerMetrics ??
          ({
            processed: 0,
            droppedBacklog: 0,
            discardedPaused: this.pausedDiscarded,
            errors: 0,
            queueLength: 0,
            maxQueue: 2,
            lastSourceTimestamp: null,
            lastOutputTimestamp: null,
            timestampMatches: true,
            lastSourceWidth: 0,
            lastSourceHeight: 0,
            lastOutputWidth: 0,
            lastOutputHeight: 0,
            paused: false,
          } satisfies PipelineMetrics),
      };
    }
    report.sourceStopped = sourceStopped;
    report.outputStopped = outputStopped;
    report.readerCancelled = readerCancelled;
    report.writerClosed = writerClosed;
    report.framesClosed += this.mainFramesClosed;
    report.metrics = this.collectMetrics(report.metrics);
    return report;
  }

  dispose(): void {
    this.stopped = true;
    clearInterval(this.metricsTimer);
    try {
      this.outputTrack?.stop();
      this.sourceTrack?.stop();
    } catch {
      // Ignore tracks already detached during teardown.
    }
    this.pendingFrame?.reject(new Error("兼容桥接管线已释放。"));
    this.pendingFrame = null;
    this.worker.terminate();
  }
}
