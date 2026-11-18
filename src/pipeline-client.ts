import type {
  CapabilityReport,
  PipelineConfig,
  PipelineMetrics,
  PipelineStopReport,
  WorkerInboundMessage,
  WorkerOutboundMessage,
} from "./types";

type PipelineErrorHandler = (message: string, code: string) => void;
type MetricsHandler = (metrics: PipelineMetrics) => void;

interface Waiter<T> {
  resolve: (value: T) => void;
  reject: (error: Error) => void;
  timer: number;
}

export class PipelineClient {
  private readonly worker: Worker;
  private readyWaiter: Waiter<MediaStreamTrack> | null = null;
  private capabilitiesWaiter: Waiter<CapabilityReport> | null = null;
  private stopWaiter: Waiter<PipelineStopReport> | null = null;
  private outputTrack: MediaStreamTrack | null = null;
  private stopped = false;
  onError: PipelineErrorHandler | null = null;
  onMetrics: MetricsHandler | null = null;

  constructor() {
    this.worker = new Worker(new URL("./pipeline.worker.ts", import.meta.url), {
      type: "module",
      name: "timestamp-video-pipeline",
    });
    this.worker.onmessage = (event: MessageEvent<WorkerOutboundMessage>) => {
      this.handleMessage(event.data);
    };
    this.worker.onerror = (event) => {
      this.rejectAll(new Error(event.message || "视频管线 Worker 启动失败。"));
      this.onError?.(event.message || "视频管线 Worker 启动失败。", "worker-crash");
    };
  }

  private post(message: WorkerInboundMessage, transfer: Transferable[] = []): void {
    this.worker.postMessage(message, transfer);
  }

  private rejectAll(error: Error): void {
    for (const waiter of [this.readyWaiter, this.capabilitiesWaiter, this.stopWaiter]) {
      if (!waiter) continue;
      clearTimeout(waiter.timer);
      waiter.reject(error);
    }
    this.readyWaiter = null;
    this.capabilitiesWaiter = null;
    this.stopWaiter = null;
  }

  private waitFor<T>(
    kind: "ready" | "capabilities" | "stop",
    timeoutMs: number,
    timeoutMessage: string,
  ): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const timer = window.setTimeout(() => {
        if (kind === "ready") this.readyWaiter = null;
        if (kind === "capabilities") this.capabilitiesWaiter = null;
        if (kind === "stop") this.stopWaiter = null;
        reject(new Error(timeoutMessage));
      }, timeoutMs);
      const waiter: Waiter<T> = {
        resolve: resolve as (value: T) => void,
        reject,
        timer,
      };
      if (kind === "ready") this.readyWaiter = waiter as Waiter<MediaStreamTrack>;
      if (kind === "capabilities") {
        this.capabilitiesWaiter = waiter as Waiter<CapabilityReport>;
      }
      if (kind === "stop") this.stopWaiter = waiter as Waiter<PipelineStopReport>;
    });
  }

  private resolveWaiter<T>(waiter: Waiter<T> | null, value: T): void {
    if (!waiter) return;
    clearTimeout(waiter.timer);
    waiter.resolve(value);
  }

  private handleMessage(message: WorkerOutboundMessage): void {
    switch (message.type) {
      case "ready": {
        this.outputTrack = message.track;
        const waiter = this.readyWaiter;
        this.readyWaiter = null;
        this.resolveWaiter(waiter, message.track);
        break;
      }
      case "capabilities": {
        const waiter = this.capabilitiesWaiter;
        this.capabilitiesWaiter = null;
        this.resolveWaiter(waiter, message.capabilities);
        break;
      }
      case "metrics":
        this.onMetrics?.(message.metrics);
        break;
      case "stopped": {
        this.stopped = true;
        const waiter = this.stopWaiter;
        this.stopWaiter = null;
        this.resolveWaiter(waiter, message.report);
        break;
      }
      case "error":
        if (message.code === "source-ended") return;
        this.onError?.(message.message, message.code);
        if (this.readyWaiter) {
          const waiter = this.readyWaiter;
          this.readyWaiter = null;
          clearTimeout(waiter.timer);
          waiter.reject(new Error(message.message));
        }
        break;
    }
  }

  async probe(): Promise<CapabilityReport> {
    const promise = this.waitFor<CapabilityReport>(
      "capabilities",
      3_000,
      "浏览器能力探测超时。",
    );
    this.post({ type: "probe" });
    return promise;
  }

  async start(sourceTrack: MediaStreamTrack, config: PipelineConfig): Promise<MediaStreamTrack> {
    const promise = this.waitFor<MediaStreamTrack>(
      "ready",
      8_000,
      "视频管线启动超时，源轨道未被处理。",
    );
    this.post({ type: "start", track: sourceTrack, config }, [
      sourceTrack as unknown as Transferable,
    ]);
    return promise;
  }

  configure(config: Partial<PipelineConfig>): void {
    if (this.stopped) return;
    this.post({ type: "configure", config });
  }

  pause(): void {
    if (this.stopped) return;
    this.post({ type: "pause" });
  }

  resume(): void {
    if (this.stopped) return;
    this.post({ type: "resume" });
  }

  async stop(): Promise<PipelineStopReport | null> {
    if (this.stopped) {
      this.outputTrack?.stop();
      return null;
    }
    const promise = this.waitFor<PipelineStopReport>(
      "stop",
      5_000,
      "视频管线停止超时。",
    );
    this.post({ type: "stop" });
    let report: PipelineStopReport | null = null;
    try {
      report = await promise;
    } catch {
      report = null;
    }
    this.stopped = true;
    try {
      this.outputTrack?.stop();
    } catch {
      // The generated track can already be detached when the worker ends.
    }
    if (report && this.outputTrack?.readyState === "ended") {
      report.outputStopped = true;
    }
    this.worker.terminate();
    return report;
  }

  dispose(): void {
    this.rejectAll(new Error("视频管线已释放。"));
    try {
      this.outputTrack?.stop();
    } catch {
      // Ignore detached tracks during teardown.
    }
    this.worker.terminate();
  }
}
