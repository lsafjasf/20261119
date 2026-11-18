import type { ValidationCheck, ValidationReport } from "./types";
import { inspectWebM, measuredDurationMs, type WebMInspection } from "./webm";

interface FrameStats {
  lumaStdDev: number;
  watermarkStdDev: number;
  uniqueColors: number;
  width: number;
  height: number;
}

interface MediaSnapshot {
  videoWidth: number;
  videoHeight: number;
  durationMs: number;
  samples: FrameStats[];
  warnings: string[];
}

interface AudioSnapshot {
  channels: number;
  durationMs: number;
  sampleRate: number;
}

function waitForEvent(
  target: EventTarget,
  eventName: string,
  timeoutMs: number,
  errorMessage: string,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      target.removeEventListener(eventName, onEvent);
      reject(new Error(errorMessage));
    }, timeoutMs);
    const onEvent = (): void => {
      clearTimeout(timer);
      target.removeEventListener(eventName, onEvent);
      resolve();
    };
    target.addEventListener(eventName, onEvent, { once: true });
  });
}

async function seekVideo(video: HTMLVideoElement, time: number): Promise<void> {
  if (Math.abs(video.currentTime - time) < 0.01) return;
  const seeked = waitForEvent(video, "seeked", 4_000, "视频定位超时，无法抽帧检查画面。");
  video.currentTime = time;
  await seeked;
}

function calculateLuma(
  pixels: Uint8ClampedArray,
  startIndex: number,
  endIndex: number,
): { mean: number; standardDeviation: number } {
  let sum = 0;
  let sumSquares = 0;
  let count = 0;
  for (let index = startIndex; index < endIndex; index += 4) {
    const luma = pixels[index] * 0.2126 + pixels[index + 1] * 0.7152 + pixels[index + 2] * 0.0722;
    sum += luma;
    sumSquares += luma * luma;
    count += 1;
  }
  if (count === 0) return { mean: 0, standardDeviation: 0 };
  const mean = sum / count;
  return {
    mean,
    standardDeviation: Math.sqrt(Math.max(0, sumSquares / count - mean * mean)),
  };
}

function analyzeCurrentFrame(video: HTMLVideoElement): FrameStats {
  const width = Math.min(480, video.videoWidth);
  const height = Math.max(2, Math.round((width / video.videoWidth) * video.videoHeight));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) throw new Error("无法创建视频校验画布。");
  context.drawImage(video, 0, 0, width, height);
  const pixels = context.getImageData(0, 0, width, height).data;
  const fullLuma = calculateLuma(pixels, 0, pixels.length);

  const watermarkStartY = Math.floor(height * 0.68);
  const watermarkEndX = Math.floor(width * 0.5);
  let watermarkSum = 0;
  let watermarkSquares = 0;
  let watermarkCount = 0;
  for (let y = watermarkStartY; y < height; y += 1) {
    for (let x = 0; x < watermarkEndX; x += 1) {
      const index = (y * width + x) * 4;
      const luma =
        pixels[index] * 0.2126 + pixels[index + 1] * 0.7152 + pixels[index + 2] * 0.0722;
      watermarkSum += luma;
      watermarkSquares += luma * luma;
      watermarkCount += 1;
    }
  }
  const watermarkMean = watermarkCount > 0 ? watermarkSum / watermarkCount : 0;
  const watermarkStdDev =
    watermarkCount > 0
      ? Math.sqrt(Math.max(0, watermarkSquares / watermarkCount - watermarkMean * watermarkMean))
      : 0;

  const colors = new Set<number>();
  for (let index = 0; index < pixels.length; index += 16) {
    const key =
      ((pixels[index] >> 4) << 8) |
      ((pixels[index + 1] >> 4) << 4) |
      (pixels[index + 2] >> 4);
    colors.add(key);
  }
  return {
    lumaStdDev: fullLuma.standardDeviation,
    watermarkStdDev,
    uniqueColors: colors.size,
    width: video.videoWidth,
    height: video.videoHeight,
  };
}

async function inspectMedia(blob: Blob, parserDurationMs: number | null): Promise<MediaSnapshot> {
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.preload = "auto";
  const url = URL.createObjectURL(blob);
  const warnings: string[] = [];
  try {
    const metadataLoaded = waitForEvent(
      video,
      "loadedmetadata",
      8_000,
      "无法读取录制文件的媒体元数据，文件可能不完整。",
    );
    video.src = url;
    video.load();
    await metadataLoaded;
    if (video.videoWidth === 0 || video.videoHeight === 0) {
      throw new Error("录制文件没有可解码的视频画面。");
    }

    let durationMs =
      Number.isFinite(video.duration) && video.duration > 0
        ? video.duration * 1000
        : parserDurationMs ?? 0;
    if (!Number.isFinite(video.duration) || video.duration <= 0) {
      warnings.push("媒体元素未提供有限时长，已通过跳到文件末尾读取真实时长。");
      try {
        await seekVideo(video, 1_000_000);
        if (Number.isFinite(video.currentTime) && video.currentTime > 0) {
          durationMs = Math.max(durationMs, video.currentTime * 1000);
        }
        await seekVideo(video, 0);
      } catch (error) {
        warnings.push(error instanceof Error ? error.message : String(error));
      }
    }

    const loadedData = waitForEvent(
      video,
      "loadeddata",
      5_000,
      "视频首帧解码超时。",
    );
    video.load();
    await loadedData;
    const ratio = durationMs > 200 ? [0.08, 0.5, 0.92] : [0];
    const seekableDurationSeconds =
      Number.isFinite(video.duration) && video.duration > 0
        ? video.duration
        : durationMs / 1000;
    const samples: FrameStats[] = [];
    for (const point of ratio) {
      if (seekableDurationSeconds > 0 && Number.isFinite(seekableDurationSeconds)) {
        const target = Math.min(
          Math.max(0, seekableDurationSeconds - 0.03),
          seekableDurationSeconds * point,
        );
        await seekVideo(video, target);
      }
      samples.push(analyzeCurrentFrame(video));
    }
    return {
      videoWidth: video.videoWidth,
      videoHeight: video.videoHeight,
      durationMs,
      samples,
      warnings,
    };
  } finally {
    video.removeAttribute("src");
    video.load();
    URL.revokeObjectURL(url);
  }
}

async function decodeAudio(blob: Blob): Promise<AudioSnapshot> {
  const AudioContextClass = window.AudioContext;
  if (!AudioContextClass) throw new Error("当前浏览器不支持 Web Audio，无法检查音频声道。");
  const context = new AudioContextClass();
  try {
    const buffer = await context.decodeAudioData(await blob.arrayBuffer());
    return {
      channels: buffer.numberOfChannels,
      durationMs: buffer.duration * 1000,
      sampleRate: buffer.sampleRate,
    };
  } finally {
    await context.close();
  }
}

function durationCheck(
  measuredMs: number,
  expectedMs: number | null,
  source: string,
): ValidationCheck {
  const toleranceMs = expectedMs === null ? 0 : Math.max(1_500, expectedMs * 0.6);
  const withinExpected =
    expectedMs === null ||
    (measuredMs >= expectedMs - toleranceMs && measuredMs <= expectedMs + toleranceMs);
  const passed = measuredMs >= 250 && withinExpected;
  const details = [
    `实际时长：${(measuredMs / 1000).toFixed(2)} 秒（${source}）`,
    expectedMs === null
      ? "录制元数据未记录有效时长，只校验文件本身。"
      : `录制活动时长：${(expectedMs / 1000).toFixed(2)} 秒，允许偏差 ±${(toleranceMs / 1000).toFixed(1)} 秒。`,
  ];
  if (measuredMs < 250) details.push("文件短于 0.25 秒，不能视为有效录制。");
  if (!withinExpected) details.push("文件时长与本次录制活动时长明显不一致。");
  return { id: "duration", label: "时长", passed, details };
}

function audioCheck(
  inspection: WebMInspection,
  decoded: AudioSnapshot | null,
  expectedChannels: number,
): ValidationCheck {
  const containerChannels = inspection.audio?.channels ?? 0;
  const actualChannels = decoded?.channels ?? containerChannels;
  const details = [
    `容器音频轨：${inspection.audio ? inspection.audio.codecId || "未知编码" : "缺失"}`,
    `实际声道：${actualChannels || "无法确定"}`,
    `采样率：${decoded?.sampleRate ?? inspection.audio?.sampleRate ?? "未知"} Hz`,
  ];
  if (expectedChannels > 0) details.push(`录制时预期声道：${expectedChannels}`);
  const passed =
    Boolean(inspection.audio) && actualChannels > 0 && (!decoded || decoded.durationMs > 100);
  if (!inspection.audio) details.push("未找到音频轨，无法确认麦克风声音已写入文件。");
  if (expectedChannels > 0 && actualChannels > 0 && actualChannels !== expectedChannels) {
    details.push("实际声道数与录制设备上报值不同，浏览器可能进行了混音。");
  }
  return { id: "audio", label: "音频声道", passed, details };
}

function videoCheck(inspection: WebMInspection, media: MediaSnapshot): ValidationCheck {
  const nonBlankSamples = media.samples.filter(
    (sample) =>
      sample.uniqueColors > 8 &&
      (sample.lumaStdDev > 2 || sample.watermarkStdDev > 3),
  ).length;
  const watermarkSamples = media.samples.filter((sample) => sample.watermarkStdDev > 3).length;
  const passed =
    Boolean(inspection.video) &&
    media.videoWidth > 0 &&
    media.videoHeight > 0 &&
    media.samples.length > 0 &&
    nonBlankSamples > 0 &&
    watermarkSamples > 0;
  const details = [
    `容器视频轨：${inspection.video?.codecId || "缺失"}`,
    `解码分辨率：${media.videoWidth}×${media.videoHeight}`,
    `已解码抽样帧：${media.samples.length}，非空帧：${nonBlankSamples}`,
    `左下角水印可辨识样本：${watermarkSamples}`,
    `视频数据块：${inspection.videoFrames}`,
  ];
  if (!passed) details.push("无法确认画面和左下角时间戳水印均已写入文件。");
  return { id: "video", label: "画面与时间戳", passed, details };
}

export async function validateRecording(
  blob: Blob,
  expectedDurationMs: number | null,
  expectedChannels: number,
): Promise<ValidationReport> {
  if (blob.size === 0) {
    throw new Error("录制文件为空，无法校验或下载。");
  }
  const warnings: string[] = [];
  let inspection: WebMInspection | null = null;
  try {
    inspection = inspectWebM(await blob.arrayBuffer());
    warnings.push(...inspection.warnings);
  } catch (error) {
    warnings.push(error instanceof Error ? error.message : String(error));
  }

  let media: MediaSnapshot;
  try {
    media = await inspectMedia(blob, inspection ? measuredDurationMs(inspection) : null);
  } catch (error) {
    throw new Error(
      `下载前画面检查失败：${error instanceof Error ? error.message : String(error)}`,
    );
  }
  warnings.push(...media.warnings);

  let decodedAudio: AudioSnapshot | null = null;
  try {
    decodedAudio = await decodeAudio(blob.slice(0, blob.size, blob.type));
  } catch (error) {
    warnings.push(
      `Web Audio 无法解码音频轨，已改用 WebM 容器字段检查声道：${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  }

  if (!inspection) {
    const durationMs = media.durationMs;
    const checks: ValidationCheck[] = [
      durationCheck(durationMs, expectedDurationMs, "媒体元素"),
      {
        id: "audio",
        label: "音频声道",
        passed: Boolean(decodedAudio && decodedAudio.channels > 0),
        details: [
          `实际声道：${decodedAudio?.channels ?? "无法确定"}`,
          "WebM 结构解析失败，无法核对容器音轨字段。",
        ],
      },
      videoCheck(
        {
          container: "webm",
          durationMs: null,
          timecodeScale: 1_000_000,
          video: null,
          audio: null,
          tracks: [],
          videoFrames: 0,
          audioBlocks: 0,
          maxBlockTimestampMs: null,
          warnings,
        },
        media,
      ),
    ];
    return {
      ok: false,
      durationMs,
      expectedDurationMs,
      checks,
      fileSize: blob.size,
      mimeType: blob.type,
      warnings,
    };
  }

  const durationMs = Math.max(media.durationMs, measuredDurationMs(inspection) ?? 0);
  const checks = [
    durationCheck(durationMs, expectedDurationMs, "媒体元素与 WebM 时间戳"),
    audioCheck(inspection, decodedAudio, expectedChannels),
    videoCheck(inspection, media),
  ];
  return {
    ok: checks.every((check) => check.passed),
    durationMs,
    expectedDurationMs,
    checks,
    fileSize: blob.size,
    mimeType: blob.type,
    warnings,
  };
}
