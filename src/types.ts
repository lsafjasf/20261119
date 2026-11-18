export interface PipelineConfig {
  scale: number;
  delayMs: number;
  maxQueue: number;
}

export interface PipelineMetrics {
  processed: number;
  droppedBacklog: number;
  discardedPaused: number;
  errors: number;
  queueLength: number;
  maxQueue: number;
  lastSourceTimestamp: number | null;
  lastOutputTimestamp: number | null;
  timestampMatches: boolean;
  lastSourceWidth: number;
  lastSourceHeight: number;
  lastOutputWidth: number;
  lastOutputHeight: number;
  paused: boolean;
}

export interface PipelineStopReport {
  sourceStopped: boolean;
  outputStopped: boolean;
  framesClosed: number;
  queueClosed: number;
  pendingFrameClosed: boolean;
  readerCancelled: boolean;
  writerClosed: boolean;
  metrics: PipelineMetrics;
}

export interface CapabilityReport {
  mediaStreamTrackProcessor: boolean;
  videoTrackGenerator: boolean;
  mediaStreamTrackGenerator: boolean;
  videoFrame: boolean;
  offscreenCanvas: boolean;
  mediaRecorder: boolean;
  supported: boolean;
  missing: string[];
}

export interface WorkerReadyMessage {
  type: "ready";
  track: MediaStreamTrack;
  config: PipelineConfig;
}

export interface WorkerMetricsMessage {
  type: "metrics";
  metrics: PipelineMetrics;
}

export interface WorkerStoppedMessage {
  type: "stopped";
  report: PipelineStopReport;
}

export interface WorkerErrorMessage {
  type: "error";
  code: "api-unavailable" | "pipeline-failed" | "source-ended";
  message: string;
}

export interface WorkerCapabilitiesMessage {
  type: "capabilities";
  capabilities: CapabilityReport;
}

export interface WorkerBridgeReadyMessage {
  type: "bridge-ready";
  config: PipelineConfig;
}

export interface WorkerBridgeFrameMessage {
  type: "bridge-frame-done";
  sequence: number;
  frame: VideoFrame | null;
  metrics: PipelineMetrics;
}

export type WorkerOutboundMessage =
  | WorkerReadyMessage
  | WorkerMetricsMessage
  | WorkerStoppedMessage
  | WorkerErrorMessage
  | WorkerCapabilitiesMessage
  | WorkerBridgeReadyMessage
  | WorkerBridgeFrameMessage;

export type WorkerInboundMessage =
  | { type: "probe" }
  | {
      type: "start";
      track: MediaStreamTrack;
      config: PipelineConfig;
    }
  | { type: "start-bridge"; config: PipelineConfig }
  | { type: "bridge-frame"; sequence: number; frame: VideoFrame }
  | { type: "configure"; config: Partial<PipelineConfig> }
  | { type: "pause" }
  | { type: "resume" }
  | { type: "stop" };

export interface ValidationCheck {
  id: "duration" | "audio" | "video";
  label: string;
  passed: boolean;
  details: string[];
}

export interface ValidationReport {
  ok: boolean;
  durationMs: number;
  expectedDurationMs: number | null;
  checks: ValidationCheck[];
  fileSize: number;
  mimeType: string;
  warnings: string[];
}

export interface RecordingSession {
  id: string;
  createdAt: number;
  updatedAt: number;
  status: "recording" | "paused" | "stopped" | "interrupted";
  mimeType: string;
  expectedChannels: number;
  activeDurationMs: number;
  chunkCount: number;
  sourceKind: "camera" | "synthetic";
  resolution: string;
}

export interface StoredChunk {
  key: string;
  sessionId: string;
  sequence: number;
  blob: Blob;
  createdAt: number;
}
