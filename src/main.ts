import { PipelineClient } from "./pipeline-client";
import { BridgePipelineClient, canUseBridgeMode } from "./bridge-pipeline-client";
import {
  appendChunk,
  deleteRecording,
  loadLatestRecording,
  newSession,
  saveSession,
} from "./storage";
import { createSyntheticSource, type SyntheticSource } from "./synthetic-source";
import type {
  CapabilityReport,
  PipelineMetrics,
  PipelineStopReport,
  RecordingSession,
  StoredChunk,
  ValidationReport,
} from "./types";
import { validateRecording } from "./validation";

type AppStatus =
  | "idle"
  | "starting"
  | "recording"
  | "paused"
  | "stopping"
  | "recovered";

interface RecoveredRecording {
  session: RecordingSession;
  chunks: StoredChunk[];
  blob: Blob;
}

interface TrackCleanupReport {
  allEnded: boolean;
  tracks: Array<{ kind: string; state: MediaStreamTrackState }>;
}

interface SelfTestResult {
  passed: boolean;
  checks: Array<{ name: string; passed: boolean; detail: string }>;
  validation: ValidationReport | null;
  metrics: PipelineMetrics | null;
  stopReport: PipelineStopReport | null;
}

interface RecorderTestApi {
  startSynthetic: () => Promise<void>;
  pause: () => Promise<void>;
  resume: () => Promise<void>;
  stop: () => Promise<void>;
  validateOnly: () => Promise<ValidationReport | null>;
  runSelfTest: () => Promise<SelfTestResult>;
  getState: () => unknown;
}

declare global {
  interface Window {
    __recorderTestApi?: RecorderTestApi;
  }
}

function element<T extends HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (!found) throw new Error(`缺少页面元素 #${id}`);
  return found as T;
}

function formatDuration(milliseconds: number): string {
  const safeMs = Math.max(0, milliseconds);
  const minutes = Math.floor(safeMs / 60_000);
  const seconds = Math.floor((safeMs % 60_000) / 1000);
  const tenths = Math.floor((safeMs % 1000) / 100);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${tenths}`;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function permissionMessage(error: unknown): string {
  const name = error instanceof DOMException ? error.name : "";
  if (name === "NotAllowedError" || name === "PermissionDeniedError") {
    return "摄像头或麦克风权限被拒绝。请在地址栏的站点设置中允许摄像头和麦克风，然后重新点击“授权并开始”。";
  }
  if (name === "SecurityError") {
    return "浏览器阻止了媒体访问。请使用 HTTPS 或 localhost 打开本页，并确认站点权限未被策略禁用。";
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") {
    return "没有找到可用的摄像头或麦克风。请连接设备，确认系统隐私设置允许浏览器访问后重试。";
  }
  if (name === "NotReadableError" || name === "TrackStartError") {
    return "摄像头或麦克风正被其他程序占用，浏览器无法读取。请关闭占用设备的应用后重试。";
  }
  if (name === "OverconstrainedError") {
    return "当前摄像头不满足请求的分辨率或设备约束。请更换设备或放宽系统摄像头设置。";
  }
  return `无法启动摄像头：${error instanceof Error ? error.message : String(error)}`;
}

function describeCapabilityFailure(capabilities: CapabilityReport): string {
  return `当前浏览器无法运行帧处理管线：缺少 ${capabilities.missing.join(
    "、",
  )}。请使用最新版 Chrome 或 Edge，并通过 HTTPS 或 localhost 打开页面。`;
}

class RecorderApp {
  private readonly preview = element<HTMLVideoElement>("preview");
  private readonly previewEmpty = element<HTMLDivElement>("preview-empty");
  private readonly startButton = element<HTMLButtonElement>("start-camera");
  private readonly pauseButton = element<HTMLButtonElement>("pause-resume");
  private readonly stopButton = element<HTMLButtonElement>("stop");
  private readonly alertPanel = element<HTMLDivElement>("alert-panel");
  private readonly capabilityBadge = element<HTMLDivElement>("capability-badge");
  private readonly recordIndicator = element<HTMLDivElement>("record-indicator");
  private readonly recordState = element<HTMLSpanElement>("record-state");
  private readonly toast = element<HTMLDivElement>("toast");
  private readonly scaleInput = element<HTMLInputElement>("scale");
  private readonly scaleValue = element<HTMLOutputElement>("scale-value");
  private readonly delayInput = element<HTMLInputElement>("delay");
  private readonly delayValue = element<HTMLOutputElement>("delay-value");
  private readonly recordingTime = element<HTMLElement>("recording-time");
  private readonly sourceNote = element<HTMLDivElement>("source-note");
  private readonly metricProcessed = element<HTMLElement>("metric-processed");
  private readonly metricDropped = element<HTMLElement>("metric-dropped");
  private readonly metricQueue = element<HTMLElement>("metric-queue");
  private readonly metricTimestamp = element<HTMLElement>("metric-timestamp");
  private readonly recoveryStatus = element<HTMLSpanElement>("recovery-status");
  private readonly recoveryContent = element<HTMLDivElement>("recovery-content");
  private readonly selfTestButton = element<HTMLButtonElement>("run-selftest");
  private readonly selfTestResult = element<HTMLDivElement>("selftest-result");
  private readonly validationSection = element<HTMLElement>("validation-section");
  private readonly validationBadge = element<HTMLSpanElement>("validation-badge");
  private readonly validationReport = element<HTMLDivElement>("validation-report");

  private status: AppStatus = "idle";
  private pipeline: PipelineClient | BridgePipelineClient | null = null;
  private sourceCleanup: (() => Promise<void>) | null = null;
  private outputTrack: MediaStreamTrack | null = null;
  private audioTracks: MediaStreamTrack[] = [];
  private recorder: MediaRecorder | null = null;
  private session: RecordingSession | null = null;
  private chunks: Blob[] = [];
  private chunkSequence = 0;
  private persistenceQueue: Promise<void> = Promise.resolve();
  private activeStartedAt = 0;
  private activeAccumulatedMs = 0;
  private lastMetrics: PipelineMetrics | null = null;
  private lastStopReport: PipelineStopReport | null = null;
  private lastValidation: ValidationReport | null = null;
  private lastTrackCleanup: TrackCleanupReport | null = null;
  private recovered: RecoveredRecording | null = null;
  private lastSelfTest: SelfTestResult | null = null;
  private capabilities: CapabilityReport | null = null;
  private bridgeMode = false;
  private busy = false;
  private toastTimer = 0;
  private lastPersistedAt = 0;
  private lastUiTick = 0;

  constructor() {
    this.bindEvents();
  }

  private bindEvents(): void {
    this.startButton.addEventListener("click", () => void this.startCamera());
    this.pauseButton.addEventListener("click", () => {
      if (this.status === "recording") void this.pause();
      if (this.status === "paused") void this.resume();
    });
    this.stopButton.addEventListener("click", () => void this.stop());
    this.selfTestButton.addEventListener("click", () => void this.runSelfTest());
    this.scaleInput.addEventListener("input", () => {
      this.scaleValue.value = `${this.scaleInput.value}%`;
      this.pipeline?.configure({ scale: Number(this.scaleInput.value) / 100 });
    });
    this.delayInput.addEventListener("input", () => {
      this.delayValue.value = `${this.delayInput.value} ms`;
      this.pipeline?.configure({ delayMs: Number(this.delayInput.value) });
    });
    window.addEventListener("pagehide", () => {
      if (this.recorder?.state === "recording") {
        try {
          this.recorder.requestData();
        } catch {
          // The browser may already be tearing down media capture.
        }
      }
    });
  }

  async init(): Promise<void> {
    this.updateUi();
    window.setInterval(() => this.tick(), 100);
    await this.probeCapabilities();
    this.updateUi();
    await this.restoreLatest();
    this.exposeTestApi();
  }

  private async probeCapabilities(): Promise<void> {
    if (!window.isSecureContext) {
      this.showCapabilityError(
        "当前页面不是安全上下文。请使用 HTTPS 或 localhost 打开，否则浏览器不会提供摄像头和帧处理接口。",
      );
      return;
    }
    if (!("Worker" in window) || typeof Worker === "undefined") {
      this.showCapabilityError("当前浏览器不支持 Web Worker，无法在后台处理摄像头帧。");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia && !new URLSearchParams(location.search).has("test")) {
      this.showCapabilityError(
        "当前浏览器未提供 navigator.mediaDevices.getUserMedia。请使用最新版 Chrome 或 Edge，并检查站点权限策略。",
      );
      return;
    }
    if (typeof MediaRecorder === "undefined") {
      this.showCapabilityError("当前浏览器不支持 MediaRecorder，无法生成可下载的录制文件。");
      return;
    }

    const probe = new PipelineClient();
    try {
      this.capabilities = await probe.probe();
    } catch (error) {
      this.showCapabilityError(
        `视频管线接口探测失败：${error instanceof Error ? error.message : String(error)}`,
      );
      return;
    } finally {
      probe.dispose();
    }
    if (!this.capabilities.supported) {
      const coreMissing = this.capabilities.missing.some(
        (name) => name === "VideoFrame" || name === "OffscreenCanvas",
      );
      if (!canUseBridgeMode() || coreMissing) {
        this.showCapabilityError(describeCapabilityFailure(this.capabilities));
        return;
      }
      this.bridgeMode = true;
      this.capabilityBadge.className = "badge badge-warn";
      this.capabilityBadge.textContent = "兼容模式 · Worker 处理";
      this.showAlert(
        "当前浏览器没有在 Worker 内暴露 MediaStreamTrackProcessor/VideoTrackGenerator。" +
          "已启用兼容桥接：主线程只负责轨道 I/O，缩放、时间戳水印、延迟与帧释放仍在 Worker 中执行。",
      );
      this.selfTestButton.disabled = false;
      return;
    }
    this.capabilityBadge.className = "badge badge-good";
    const generator = this.capabilities.videoTrackGenerator
      ? "VideoTrackGenerator"
      : "MediaStreamTrackGenerator（Worker 兼容 API）";
    this.capabilityBadge.textContent = `原生 Worker 管线 · ${generator}`;
    this.selfTestButton.disabled = false;
  }

  private showCapabilityError(message: string): void {
    this.capabilityBadge.className = "badge badge-bad";
    this.capabilityBadge.textContent = "接口不可用";
    this.startButton.disabled = true;
    this.selfTestButton.disabled = true;
    this.showAlert(message);
  }

  private pipelineUsable(): boolean {
    return Boolean(this.capabilities && (this.capabilities.supported || this.bridgeMode));
  }

  private updateUi(): void {
    const recording = this.status === "recording";
    const paused = this.status === "paused";
    const active = recording || paused || this.status === "starting" || this.status === "stopping";
    this.startButton.disabled = this.busy || active || !this.pipelineUsable();
    this.pauseButton.disabled = this.busy || (!recording && !paused);
    this.stopButton.disabled = this.busy || (!recording && !paused);
    this.pauseButton.textContent = paused ? "继续录制" : "暂停";
    this.selfTestButton.disabled = this.busy || active || !this.pipelineUsable();

    this.recordIndicator.classList.toggle("is-recording", recording);
    this.recordIndicator.classList.toggle("is-paused", paused);
    if (recording) this.recordState.textContent = "录制中";
    else if (paused) this.recordState.textContent = "已暂停";
    else if (this.status === "starting") this.recordState.textContent = "正在启动";
    else if (this.status === "stopping") this.recordState.textContent = "正在保存";
    else if (this.status === "recovered") this.recordState.textContent = "已恢复录制";
    else this.recordState.textContent = "未录制";

    if (recording || paused || this.status === "stopping") {
      this.recordingTime.textContent = formatDuration(this.activeDuration());
    }
    this.previewEmpty.hidden = this.preview.srcObject !== null;
  }

  private tick(): void {
    const now = performance.now();
    if (now - this.lastUiTick < 100) return;
    this.lastUiTick = now;
    if (this.status === "recording" || this.status === "paused") {
      this.recordingTime.textContent = formatDuration(this.activeDuration());
      if (this.session && now - this.lastPersistedAt > 1_000) {
        this.lastPersistedAt = now;
        this.session.activeDurationMs = this.activeDuration();
        void saveSession(this.session).catch((error: unknown) => {
          this.showAlert(
            `录制时长元数据暂未写入 IndexedDB：${
              error instanceof Error ? error.message : String(error)
            }`,
          );
        });
      }
    }
  }

  private activeDuration(): number {
    if (this.status === "recording") {
      return this.activeAccumulatedMs + performance.now() - this.activeStartedAt;
    }
    return this.activeAccumulatedMs;
  }

  private showAlert(message: string): void {
    this.alertPanel.hidden = false;
    this.alertPanel.textContent = message;
  }

  private clearAlert(): void {
    this.alertPanel.hidden = true;
    this.alertPanel.textContent = "";
  }

  private showToast(message: string, isError = false): void {
    clearTimeout(this.toastTimer);
    this.toast.textContent = message;
    this.toast.classList.toggle("is-error", isError);
    this.toast.classList.add("is-visible");
    this.toastTimer = window.setTimeout(() => {
      this.toast.classList.remove("is-visible");
    }, 4_500);
  }

  private async startCamera(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      this.showAlert(
        "当前浏览器未提供摄像头接口。请使用最新版 Chrome 或 Edge，并通过 HTTPS 或 localhost 打开页面。",
      );
      return;
    }
    await this.start("camera");
  }

  private async startSynthetic(): Promise<void> {
    await this.start("synthetic");
  }

  private chooseMimeType(): string {
    const candidates = [
      "video/webm;codecs=vp8,opus",
      "video/webm;codecs=vp9,opus",
      "video/webm;codecs=vp8",
      "video/webm",
    ];
    const supported = candidates.find((candidate) => MediaRecorder.isTypeSupported(candidate));
    if (!supported) {
      throw new Error(
        "当前浏览器无法生成 WebM（VP8/VP9 + Opus）录制文件，不能执行时长、声道和画面校验。",
      );
    }
    return supported;
  }

  private async obtainSource(kind: "camera" | "synthetic"): Promise<{
    stream: MediaStream;
    expectedChannels: number;
    resolution: string;
    cleanup: () => Promise<void>;
  }> {
    if (kind === "synthetic") {
      const source: SyntheticSource = await createSyntheticSource();
      return {
        stream: source.stream,
        expectedChannels: source.expectedChannels,
        resolution: source.resolution,
        cleanup: source.cleanup,
      };
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 1280 },
          height: { ideal: 720 },
          facingMode: "user",
        },
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (error) {
      throw new Error(permissionMessage(error));
    }
    const videoTrack = stream.getVideoTracks()[0];
    const audioTrack = stream.getAudioTracks()[0];
    if (!videoTrack) {
      for (const track of stream.getTracks()) track.stop();
      throw new Error("摄像头权限已授予，但没有返回视频轨。请检查系统摄像头是否可用。");
    }
    const videoSettings = videoTrack.getSettings();
    const resolution =
      videoSettings.width && videoSettings.height
        ? `${videoSettings.width}×${videoSettings.height}（摄像头）`
        : "摄像头";
    const expectedChannels = audioTrack?.getSettings().channelCount ?? 0;
    if (!audioTrack) {
      this.showAlert(
        "摄像头已连接，但浏览器没有返回麦克风音轨。录制仍可继续，但下载前的声道校验会失败。",
      );
    }
    return {
      stream,
      expectedChannels,
      resolution,
      cleanup: async () => {
        for (const track of stream.getTracks()) {
          try {
            track.stop();
          } catch {
            // A transferred video track can already be detached.
          }
        }
      },
    };
  }

  private async start(kind: "camera" | "synthetic"): Promise<void> {
    if (this.busy || ["recording", "paused", "starting", "stopping"].includes(this.status)) {
      return;
    }
    if (this.capabilities && !this.pipelineUsable()) {
      this.showAlert(describeCapabilityFailure(this.capabilities));
      return;
    }
    this.busy = true;
    this.status = "starting";
    this.clearAlert();
    this.updateUi();

    let pipeline: PipelineClient | BridgePipelineClient | null = null;
    let cleanup: (() => Promise<void>) | null = null;
    try {
      const source = await this.obtainSource(kind);
      cleanup = source.cleanup;
      this.sourceCleanup = cleanup;
      this.audioTracks = source.stream.getAudioTracks();
      const videoTrack = source.stream.getVideoTracks()[0];
      if (!videoTrack) throw new Error("视频源没有视频轨，无法启动处理管线。");

      pipeline = this.bridgeMode ? new BridgePipelineClient() : new PipelineClient();
      pipeline.onMetrics = (metrics) => {
        this.lastMetrics = metrics;
        this.renderMetrics(metrics);
      };
      pipeline.onError = (message, code) => {
        this.showAlert(message);
        if (
          code === "pipeline-failed" &&
          (this.status === "recording" || this.status === "paused")
        ) {
          void this.stop("pipeline-error");
        }
      };
      this.pipeline = pipeline;
      const outputTrack = await pipeline.start(videoTrack, {
        scale: Number(this.scaleInput.value) / 100,
        delayMs: Number(this.delayInput.value),
        maxQueue: 2,
      });
      this.outputTrack = outputTrack;

      const combined = new MediaStream([outputTrack, ...this.audioTracks]);
      this.preview.srcObject = combined;
      this.previewEmpty.hidden = true;
      await this.preview.play().catch(() => undefined);

      const mimeType = this.chooseMimeType();
      const recorder = new MediaRecorder(combined, {
        mimeType,
        videoBitsPerSecond: 3_000_000,
        audioBitsPerSecond: 128_000,
      });
      this.recorder = recorder;
      this.chunks = [];
      this.chunkSequence = 0;
      this.persistenceQueue = Promise.resolve();
      this.session = newSession(
        kind,
        mimeType,
        source.expectedChannels,
        source.resolution,
      );
      await saveSession(this.session);

      recorder.addEventListener("dataavailable", (event: BlobEvent) => {
        this.handleData(event.data);
      });
      recorder.addEventListener("error", (event) => {
        const recorderError = (event as Event & { error?: DOMException }).error;
        this.showAlert(
          `录制器错误：${recorderError?.message ?? "MediaRecorder 发生未知错误"}。已录制片段仍保存在本机。`,
        );
      });
      recorder.start(250);
      this.activeAccumulatedMs = 0;
      this.activeStartedAt = performance.now();
      this.lastPersistedAt = performance.now();
      this.status = "recording";
      this.sourceNote.textContent = `${source.resolution} · 有界队列 2 帧`;
      this.showToast("录制已开始，片段每 250 ms 持久化到 IndexedDB。");
    } catch (error) {
      this.showAlert(permissionMessage(error));
      await pipeline?.stop().catch(() => null);
      pipeline?.dispose();
      await cleanup?.().catch(() => undefined);
      this.resetRuntime();
      this.status = "idle";
    } finally {
      this.busy = false;
      this.updateUi();
    }
  }

  private handleData(blob: Blob): void {
    if (blob.size === 0) return;
    const sequence = this.chunkSequence;
    this.chunkSequence += 1;
    this.chunks.push(blob);
    const session = this.session;
    if (!session) return;
    const duration = this.activeDuration();
    this.persistenceQueue = this.persistenceQueue
      .then(async () => {
        session.activeDurationMs = duration;
        await appendChunk(session, sequence, blob);
        session.chunkCount = Math.max(session.chunkCount, sequence + 1);
      })
      .catch((error: unknown) => {
        this.showAlert(
          `录制片段写入 IndexedDB 失败，刷新页面后可能无法恢复：${
            error instanceof Error ? error.message : String(error)
          }`,
        );
      });
  }

  private async pause(): Promise<void> {
    if (this.status !== "recording" || !this.recorder || !this.session) return;
    this.busy = true;
    try {
      if (this.recorder.state !== "recording") {
        throw new Error(`录制器当前状态为 ${this.recorder.state}，无法暂停。`);
      }
      this.recorder.pause();
      this.pipeline?.pause();
      this.activeAccumulatedMs = this.activeDuration();
      this.session.status = "paused";
      this.session.activeDurationMs = this.activeAccumulatedMs;
      await saveSession(this.session);
      this.status = "paused";
      this.showToast("录制已暂停，Worker 会立即释放暂停期间到达的帧。");
    } catch (error) {
      this.showAlert(
        `暂停失败：${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      this.busy = false;
      this.updateUi();
    }
  }

  private async resume(): Promise<void> {
    if (this.status !== "paused" || !this.recorder || !this.session) return;
    this.busy = true;
    try {
      if (this.recorder.state !== "paused") {
        throw new Error(`录制器当前状态为 ${this.recorder.state}，无法继续。`);
      }
      this.recorder.resume();
      this.pipeline?.resume();
      this.activeStartedAt = performance.now();
      this.session.status = "recording";
      await saveSession(this.session);
      this.status = "recording";
      this.showToast("录制已继续，帧时间戳与录制片段顺序保持连续。");
    } catch (error) {
      this.showAlert(
        `继续录制失败：${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      this.busy = false;
      this.updateUi();
    }
  }

  private stopRecorder(): Promise<void> {
    const recorder = this.recorder;
    if (!recorder || recorder.state === "inactive") return Promise.resolve();
    return new Promise((resolve) => {
      const timeout = window.setTimeout(resolve, 4_000);
      recorder.addEventListener(
        "stop",
        () => {
          clearTimeout(timeout);
          resolve();
        },
        { once: true },
      );
      try {
        recorder.stop();
      } catch {
        clearTimeout(timeout);
        resolve();
      }
    });
  }

  private async stop(reason: "user" | "pipeline-error" = "user"): Promise<void> {
    if (this.status !== "recording" && this.status !== "paused") return;
    this.activeAccumulatedMs = this.activeDuration();
    this.status = "stopping";
    this.busy = true;
    this.updateUi();
    try {
      await this.stopRecorder();
      await this.persistenceQueue;
      if (this.session) {
        this.session.status = "stopped";
        this.session.activeDurationMs = this.activeAccumulatedMs;
        await saveSession(this.session);
      }

      const stopReport = await this.pipeline?.stop().catch(() => null);
      if (stopReport) this.lastStopReport = stopReport;
      this.pipeline?.dispose();
      this.pipeline = null;
      await this.sourceCleanup?.().catch(() => undefined);
      this.sourceCleanup = null;
      this.lastTrackCleanup = this.stopRemainingTracks();

      this.preview.pause();
      this.preview.srcObject = null;
      this.previewEmpty.hidden = false;
      this.recorder = null;
      this.outputTrack = null;
      this.audioTracks = [];
      this.status = "idle";

      if (this.session && this.chunks.length > 0) {
        this.recovered = {
          session: { ...this.session },
          chunks: [],
          blob: new Blob(this.chunks, { type: this.session.mimeType }),
        };
        this.renderRecovery();
      }
      this.showToast(
        reason === "pipeline-error"
          ? "管线异常后已停止并保存已录片段，请校验恢复内容。"
          : "录制已保存。下载前会检查时长、声道和画面。",
        reason === "pipeline-error",
      );
    } catch (error) {
      this.showAlert(
        `停止录制时出错：${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      this.busy = false;
      this.updateUi();
    }
  }

  private stopRemainingTracks(): TrackCleanupReport {
    const tracks: MediaStreamTrack[] = [
      ...(this.outputTrack ? [this.outputTrack] : []),
      ...this.audioTracks,
    ];
    for (const track of tracks) {
      try {
        track.stop();
      } catch {
        // Ignore tracks already detached by the worker.
      }
    }
    const details = tracks.map((track) => ({
      kind: track.kind,
      state: track.readyState,
    }));
    return {
      allEnded: details.every((track) => track.state === "ended"),
      tracks: details,
    };
  }

  private resetRuntime(): void {
    this.pipeline?.dispose();
    this.pipeline = null;
    this.outputTrack = null;
    this.audioTracks = [];
    this.recorder = null;
    this.session = null;
    this.preview.srcObject = null;
    this.previewEmpty.hidden = false;
    this.activeAccumulatedMs = 0;
    this.sourceNote.textContent = "尚未选择视频源";
  }

  private renderMetrics(metrics: PipelineMetrics): void {
    this.metricProcessed.textContent = String(metrics.processed);
    this.metricDropped.textContent = String(metrics.droppedBacklog);
    this.metricQueue.textContent = `${metrics.queueLength} / ${metrics.maxQueue}`;
    if (metrics.lastSourceTimestamp === null) {
      this.metricTimestamp.textContent = "等待帧";
    } else {
      this.metricTimestamp.textContent = metrics.timestampMatches ? "源时间戳一致" : "时间戳不一致";
      this.metricTimestamp.className = metrics.timestampMatches ? "metric-good" : "metric-bad";
    }
  }

  private async restoreLatest(): Promise<void> {
    try {
      this.recovered = await loadLatestRecording();
      if (this.recovered) {
        this.recoveryStatus.textContent = "可恢复";
        this.recoveryStatus.className = "status-chip";
      } else {
        this.recoveryStatus.textContent = "无记录";
        this.recoveryStatus.className = "status-chip";
      }
      this.renderRecovery();
    } catch (error) {
      this.recoveryStatus.textContent = "不可用";
      this.recoveryContent.textContent = "";
      const message = document.createElement("p");
      message.className = "muted";
      message.textContent = `无法读取 IndexedDB：${
        error instanceof Error ? error.message : String(error)
      }`;
      this.recoveryContent.append(message);
    }
  }

  private renderRecovery(): void {
    this.recoveryContent.textContent = "";
    const recording = this.recovered;
    if (!recording) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "尚无可恢复的录制。录制片段会每 250 ms 写入 IndexedDB。";
      this.recoveryContent.append(empty);
      return;
    }

    const { session, blob } = recording;
    const metadata = document.createElement("div");
    metadata.className = "recovery-meta";
    const rows = [
      ["时间", new Date(session.createdAt).toLocaleString("zh-CN", { hour12: false })],
      ["使用时长", formatDuration(session.activeDurationMs)],
      ["来源", `${session.resolution} · ${session.sourceKind === "camera" ? "摄像头" : "模拟源"}`],
      ["文件", `${formatBytes(blob.size)} · ${session.mimeType}`],
    ];
    for (const [label, value] of rows) {
      const line = document.createElement("span");
      const strong = document.createElement("strong");
      strong.textContent = `${label}：`;
      line.append(strong, document.createTextNode(value));
      metadata.append(line);
    }

    const message = document.createElement("p");
    message.className = "muted";
    message.textContent =
      session.status === "interrupted"
        ? "页面刷新或关闭时录制被中断。已持久化片段仍可校验和下载。"
        : "录制已完成。点击下方按钮后，必须先通过时长、声道与画面检查才会开始下载。";

    const actions = document.createElement("div");
    actions.className = "recovery-actions";
    const validateButton = document.createElement("button");
    validateButton.type = "button";
    validateButton.className = "button button-primary";
    validateButton.textContent = "校验并下载";
    validateButton.dataset.testid = "validate-download";
    validateButton.addEventListener("click", () => void this.validateAndDownload(recording));
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "button button-quiet";
    deleteButton.textContent = "删除记录";
    deleteButton.addEventListener("click", () => void this.removeRecovered(session.id));
    actions.append(validateButton, deleteButton);

    this.recoveryContent.append(metadata, message, actions);
  }

  private async removeRecovered(sessionId: string): Promise<void> {
    try {
      await deleteRecording(sessionId);
      if (this.recovered?.session.id === sessionId) this.recovered = null;
      this.recoveryStatus.textContent = "无记录";
      this.renderRecovery();
      this.validationSection.hidden = true;
    } catch (error) {
      this.showAlert(
        `删除本机录制失败：${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }

  private currentRecording(): RecoveredRecording | null {
    if (this.recovered) return this.recovered;
    if (this.session && this.chunks.length > 0) {
      return {
        session: { ...this.session },
        chunks: [],
        blob: new Blob(this.chunks, { type: this.session.mimeType }),
      };
    }
    return null;
  }

  private async validateCurrent(recording: RecoveredRecording): Promise<ValidationReport> {
    const report = await validateRecording(
      recording.blob,
      recording.session.activeDurationMs || null,
      recording.session.expectedChannels,
    );
    this.lastValidation = report;
    this.renderValidation(report);
    return report;
  }

  private async validateAndDownload(recording: RecoveredRecording): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    this.updateUi();
    this.clearAlert();
    try {
      const report = await this.validateCurrent(recording);
      if (!report.ok) {
        this.showAlert("下载已阻止：录制文件没有通过全部校验，请查看下方报告。");
        return;
      }
      const extension = recording.session.mimeType.includes("webm") ? "webm" : "mp4";
      const stamp = new Date(recording.session.createdAt)
        .toISOString()
        .replace(/[:.]/g, "-");
      const url = URL.createObjectURL(recording.blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `timestamp-recording-${stamp}.${extension}`;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
      this.showToast(
        `校验通过：${(report.durationMs / 1000).toFixed(2)} 秒，` +
          `${report.fileSize.toLocaleString("zh-CN")} 字节，下载已开始。`,
      );
    } catch (error) {
      this.showAlert(
        `校验失败，未下载文件：${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      this.busy = false;
      this.updateUi();
    }
  }

  private renderValidation(report: ValidationReport): void {
    this.validationSection.hidden = false;
    this.validationBadge.className = report.ok ? "badge badge-good" : "badge badge-bad";
    this.validationBadge.textContent = report.ok ? "校验通过" : "校验未通过";
    this.validationReport.textContent = "";
    for (const check of report.checks) {
      const card = document.createElement("article");
      card.className = "check-card";
      card.dataset.check = check.id;
      card.dataset.passed = String(check.passed);
      const title = document.createElement("h3");
      title.className = check.passed ? "check-pass" : "check-fail";
      const icon = document.createElement("span");
      icon.className = "check-icon";
      const iconText = document.createElement("span");
      iconText.textContent = check.passed ? "✓" : "!";
      icon.append(iconText);
      title.append(icon, document.createTextNode(check.label));
      card.append(title);
      for (const detail of check.details) {
        const line = document.createElement("p");
        line.textContent = detail;
        card.append(line);
      }
      this.validationReport.append(card);
    }
    if (report.warnings.length > 0) {
      const warningCard = document.createElement("article");
      warningCard.className = "check-card";
      const warningTitle = document.createElement("h3");
      warningTitle.textContent = "解析备注";
      warningCard.append(warningTitle);
      for (const warning of report.warnings) {
        const line = document.createElement("p");
        line.textContent = warning;
        warningCard.append(line);
      }
      this.validationReport.append(warningCard);
    }
    this.validationSection.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  private async validateOnlyCurrent(): Promise<ValidationReport | null> {
    const recording = this.currentRecording();
    if (!recording) {
      this.showAlert("没有可校验的录制文件，请先完成一次录制或恢复本机片段。");
      return null;
    }
    try {
      return await this.validateCurrent(recording);
    } catch (error) {
      this.showAlert(
        `校验失败：${error instanceof Error ? error.message : String(error)}`,
      );
      return null;
    }
  }

  private wait(milliseconds: number): Promise<void> {
    return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  }

  private readStopReport(): PipelineStopReport | null {
    return this.lastStopReport;
  }

  private readTrackCleanup(): TrackCleanupReport | null {
    return this.lastTrackCleanup;
  }

  private async runSelfTest(): Promise<SelfTestResult> {
    if (this.busy || ["recording", "paused", "starting", "stopping"].includes(this.status)) {
      throw new Error("当前有录制任务正在进行，无法启动自检。");
    }
    if (!this.pipelineUsable()) {
      const failure = this.capabilities
        ? describeCapabilityFailure(this.capabilities)
        : "浏览器能力尚未探测完成，请稍后重试。";
      this.showAlert(failure);
      return { passed: false, checks: [], validation: null, metrics: null, stopReport: null };
    }

    this.selfTestButton.disabled = true;
    this.selfTestResult.hidden = false;
    this.selfTestResult.dataset.status = "running";
    this.selfTestResult.textContent = "正在启动模拟源并录制…";
    this.scaleInput.value = "60";
    this.scaleValue.value = "60%";
    this.delayInput.value = "120";
    this.delayValue.value = "120 ms";
    this.lastStopReport = null;
    this.lastTrackCleanup = null;

    const checks: SelfTestResult["checks"] = [];
    try {
      await this.startSynthetic();
      if (this.status !== "recording") {
        throw new Error(this.alertPanel.textContent || "模拟源录制未能启动。");
      }
      await this.wait(1_700);
      await this.pause();
      await this.wait(350);
      await this.resume();
      await this.wait(1_300);
      await this.stop();
      await this.persistenceQueue;

      const stopReport = this.readStopReport();
      const trackCleanup = this.readTrackCleanup();
      const metrics = stopReport?.metrics ?? this.lastMetrics;
      const validation = await this.validateOnlyCurrent();
      const videoCheck = validation?.checks.find((check) => check.id === "video");
      const audioCheck = validation?.checks.find((check) => check.id === "audio");
      const durationCheck = validation?.checks.find((check) => check.id === "duration");
      checks.push({
        name: "有界队列发生丢帧而不是增长",
        passed: Boolean(metrics && metrics.droppedBacklog > 0 && metrics.queueLength <= metrics.maxQueue),
        detail: metrics
          ? `丢弃 ${metrics.droppedBacklog} 帧，停止时队列 ${metrics.queueLength}/${metrics.maxQueue}`
          : "没有收到管线指标",
      });
      checks.push({
        name: "输出帧保留源时间戳",
        passed: Boolean(metrics?.timestampMatches && (metrics.processed ?? 0) > 5),
        detail: metrics
          ? `源 ${metrics.lastSourceTimestamp} µs / 输出 ${metrics.lastOutputTimestamp} µs`
          : "没有时间戳指标",
      });
      checks.push({
        name: "分辨率缩放实时生效",
        passed: Boolean(
          metrics &&
            metrics.lastOutputWidth >= 380 &&
            metrics.lastOutputWidth <= 388 &&
            metrics.lastOutputHeight >= 212 &&
            metrics.lastOutputHeight <= 220,
        ),
        detail: metrics
          ? `源 ${metrics.lastSourceWidth}×${metrics.lastSourceHeight} → 输出 ${metrics.lastOutputWidth}×${metrics.lastOutputHeight}`
          : "没有分辨率指标",
      });
      checks.push({
        name: "停止时关闭帧、读取器和轨道",
        passed: Boolean(
          stopReport &&
            stopReport.framesClosed > 0 &&
            stopReport.readerCancelled &&
            stopReport.writerClosed &&
            trackCleanup?.allEnded,
        ),
        detail: stopReport
          ? `关闭帧 ${stopReport.framesClosed}，reader=${stopReport.readerCancelled}，` +
            `writer=${stopReport.writerClosed}，轨道全部结束=${trackCleanup?.allEnded}`
          : "没有停止报告",
      });
      checks.push({
        name: "暂停期间不积压帧",
        passed: Boolean(stopReport),
        detail: `暂停时释放 ${stopReport?.metrics.discardedPaused ?? 0} 帧`,
      });
      checks.push({
        name: "文件通过时长检查",
        passed: Boolean(durationCheck?.passed && (validation?.durationMs ?? 0) > 1_000),
        detail: durationCheck?.details[0] ?? "没有时长报告",
      });
      checks.push({
        name: "文件通过音频声道检查",
        passed: Boolean(audioCheck?.passed),
        detail: audioCheck?.details.slice(0, 3).join("；") ?? "没有音频报告",
      });
      checks.push({
        name: "文件通过画面与水印检查",
        passed: Boolean(videoCheck?.passed),
        detail: videoCheck?.details.slice(0, 4).join("；") ?? "没有画面报告",
      });

      const result: SelfTestResult = {
        passed: checks.every((check) => check.passed) && Boolean(validation?.ok),
        checks,
        validation,
        metrics: metrics ?? null,
        stopReport: this.lastStopReport,
      };
      this.lastSelfTest = result;
      this.renderSelfTest(result);
      return result;
    } catch (error) {
      const result: SelfTestResult = {
        passed: false,
        checks: [
          {
            name: "模拟源自检执行",
            passed: false,
            detail: `自检异常：${error instanceof Error ? error.message : String(error)}`,
          },
          ...checks,
        ],
        validation: this.lastValidation,
        metrics: this.lastMetrics,
        stopReport: this.lastStopReport,
      };
      this.lastSelfTest = result;
      this.renderSelfTest(result);
      return result;
    } finally {
      this.updateUi();
    }
  }

  private renderSelfTest(result: SelfTestResult): void {
    this.selfTestResult.hidden = false;
    this.selfTestResult.dataset.status = result.passed ? "passed" : "failed";
    this.selfTestResult.textContent = "";
    const heading = document.createElement("strong");
    heading.textContent = result.passed ? "自动验证通过" : "自动验证未通过";
    this.selfTestResult.append(heading);
    for (const check of result.checks) {
      const line = document.createElement("div");
      line.textContent = `${check.passed ? "✓" : "✗"} ${check.name}：${check.detail}`;
      line.className = check.passed ? "check-pass" : "check-fail";
      this.selfTestResult.append(line);
    }
  }

  private exposeTestApi(): void {
    if (!new URLSearchParams(location.search).has("test")) return;
    window.__recorderTestApi = {
      startSynthetic: () => this.startSynthetic(),
      pause: () => this.pause(),
      resume: () => this.resume(),
      stop: () => this.stop(),
      validateOnly: () => this.validateOnlyCurrent(),
      runSelfTest: () => this.runSelfTest(),
      getState: () => ({
        status: this.status,
        capabilities: this.capabilities,
        metrics: this.lastMetrics,
        stopReport: this.lastStopReport,
        trackCleanup: this.lastTrackCleanup,
        validation: this.lastValidation,
        selfTest: this.lastSelfTest,
        session: this.session,
        chunks: this.chunks.map((blob) => ({ size: blob.size, type: blob.type })),
        recovered: this.recovered
          ? {
              session: this.recovered.session,
              size: this.recovered.blob.size,
            }
          : null,
      }),
    };
  }
}

const app = new RecorderApp();
void app.init().catch((error: unknown) => {
  const panel = document.getElementById("alert-panel");
  if (panel) {
    panel.hidden = false;
    panel.textContent = `应用初始化失败：${
      error instanceof Error ? error.message : String(error)
    }`;
  }
});
