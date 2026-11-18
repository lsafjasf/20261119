interface MediaStreamTrackProcessorInit {
  track: MediaStreamTrack;
  maxBufferSize?: number;
}

declare class MediaStreamTrackProcessor<T = VideoFrame> {
  constructor(init: MediaStreamTrackProcessorInit);
  readonly readable: ReadableStream<T>;
  readonly discardedFrames?: number;
  readonly totalFrames?: number;
}

interface VideoTrackGeneratorInit {
  track?: MediaStreamTrack;
}

declare class VideoTrackGenerator {
  constructor(init?: VideoTrackGeneratorInit);
  readonly track: MediaStreamTrack;
  readonly writable: WritableStream<VideoFrame>;
}

interface MediaStreamTrackGeneratorInit {
  kind: "video" | "audio";
}

declare class MediaStreamTrackGenerator<T = VideoFrame> {
  constructor(init: MediaStreamTrackGeneratorInit);
  readonly writable: WritableStream<T>;
  readonly stopped: boolean;
  stop(): void;
}

interface MediaStreamTrackGenerator<T> extends MediaStreamTrack {}
