import { BufferMeter } from "./meter.js";
import { readExact } from "./wav.js";

export const DEFAULT_BLOCK_FRAMES = 16384;
export const DEFAULT_PREFETCH_BLOCKS = 2;

export function normalizeRegion(region, totalFrames) {
  const startFrame = Number.isFinite(region?.startFrame) ? Math.floor(region.startFrame) : 0;
  const endFrame = Number.isFinite(region?.endFrame)
    ? Math.floor(region.endFrame)
    : totalFrames;
  if (startFrame < 0 || endFrame < startFrame || endFrame > totalFrames) {
    throw new RangeError(`Region ${startFrame}..${endFrame} is outside 0..${totalFrames}.`);
  }
  return { startFrame, endFrame };
}

export function mapOutputFrameToSource(outputFrame, totalFrames, region) {
  if (totalFrames <= 0) return 0;
  const frame = Math.max(0, Math.min(totalFrames - 1, Math.floor(outputFrame)));
  const { startFrame, endFrame } = normalizeRegion(region, totalFrames);
  if (frame >= startFrame && frame < endFrame) {
    return endFrame - 1 - (frame - startFrame);
  }
  return frame;
}

export function mapSourceFrameToOutput(sourceFrame, totalFrames, region) {
  if (totalFrames <= 0) return 0;
  const frame = Math.max(0, Math.min(totalFrames - 1, Math.floor(sourceFrame)));
  const { startFrame, endFrame } = normalizeRegion(region, totalFrames);
  if (frame >= startFrame && frame < endFrame) {
    return startFrame + (endFrame - 1 - frame);
  }
  return frame;
}

export function reverseFrameBlock(bytes, format, frameCount) {
  const frameBytes = format.blockAlign;
  const availableFrames = Math.floor(bytes.byteLength / frameBytes);
  const count = Math.min(frameCount, availableFrames);
  const output = new Uint8Array(count * frameBytes);
  for (let frame = 0; frame < count; frame += 1) {
    const sourceOffset = (count - 1 - frame) * frameBytes;
    output.set(bytes.subarray(sourceOffset, sourceOffset + frameBytes), frame * frameBytes);
  }
  return output;
}

export function reversePcmExact(pcmBytes, format, options = {}) {
  const frameCount = options.frameCount ?? format.frames;
  const frameBytes = format.blockAlign;
  if (pcmBytes.byteLength < frameCount * frameBytes) {
    throw new RangeError("PCM buffer is shorter than the declared frame count.");
  }
  const region = normalizeRegion(options.region, frameCount);
  const blockFrames = Math.max(
    1,
    Math.floor(options.blockFrames || DEFAULT_BLOCK_FRAMES),
  );
  const output = pcmBytes.slice();
  const regionFrames = region.endFrame - region.startFrame;
  if (regionFrames === 0) return output;

  const blockCount = Math.ceil(regionFrames / blockFrames);
  for (let blockIndex = blockCount - 1; blockIndex >= 0; blockIndex -= 1) {
    const sourceStart = region.startFrame + blockIndex * blockFrames;
    const count = Math.min(blockFrames, region.endFrame - sourceStart);
    const destinationStart = region.startFrame + region.endFrame - sourceStart - count;
    for (let frame = 0; frame < count; frame += 1) {
      const sourceOffset = (sourceStart + count - 1 - frame) * frameBytes;
      const destinationOffset = (destinationStart + frame) * frameBytes;
      output.set(
        pcmBytes.subarray(sourceOffset, sourceOffset + frameBytes),
        destinationOffset,
      );
    }
  }
  return output;
}

class ReverseBlockPrefetcher {
  constructor(source, format, region, blockFrames, prefetchBlocks, meter) {
    this.source = source;
    this.format = format;
    this.region = region;
    this.blockFrames = blockFrames;
    this.prefetchBlocks = prefetchBlocks;
    this.meter = meter || new BufferMeter();
    this.entries = new Map();
    this.closed = false;
  }

  blockStart(blockIndex) {
    return this.region.startFrame + blockIndex * this.blockFrames;
  }

  blockCount(blockIndex) {
    return Math.min(this.blockFrames, this.region.endFrame - this.blockStart(blockIndex));
  }

  ensure(blockIndex) {
    if (this.closed || blockIndex < 0 || this.blockStart(blockIndex) >= this.region.endFrame) {
      return;
    }
    if (this.entries.has(blockIndex)) return;

    const frameCount = this.blockCount(blockIndex);
    const offset =
      this.format.dataOffset + this.blockStart(blockIndex) * this.format.blockAlign;
    const entry = { bytes: null, frameCount, promise: null };
    entry.promise = readExact(this.source, offset, frameCount * this.format.blockAlign)
      .then((bytes) => {
        if (this.closed) {
          throw new DOMException("Prefetch was closed.", "AbortError");
        }
        entry.bytes = bytes;
        const heldBlocks = [...this.entries.values()].filter((item) => item.bytes).length;
        this.meter.add(bytes.byteLength, heldBlocks);
        return bytes;
      })
      .catch((error) => {
        this.entries.delete(blockIndex);
        throw error;
      });
    entry.promise.catch(() => {
      // Look-ahead reads can be abandoned when playback is paused.
    });
    this.entries.set(blockIndex, entry);
  }

  async get(blockIndex) {
    this.ensure(blockIndex);
    for (let ahead = 1; ahead <= this.prefetchBlocks; ahead += 1) {
      this.ensure(blockIndex - ahead);
    }
    const entry = this.entries.get(blockIndex);
    if (!entry) {
      throw new RangeError(`Block ${blockIndex} is outside the reverse region.`);
    }
    return { bytes: await entry.promise, frameCount: entry.frameCount };
  }

  release(blockIndex) {
    const entry = this.entries.get(blockIndex);
    if (!entry) return;
    if (entry.bytes) {
      this.meter.remove(entry.bytes.byteLength);
    }
    this.entries.delete(blockIndex);
  }

  close() {
    this.closed = true;
    for (const entry of this.entries.values()) {
      if (entry.bytes) {
        this.meter.remove(entry.bytes.byteLength);
      }
    }
    this.entries.clear();
  }
}

async function* forwardChunks(source, format, startFrame, endFrame, blockFrames, meter) {
  for (let frame = startFrame; frame < endFrame; frame += blockFrames) {
    const frameCount = Math.min(blockFrames, endFrame - frame);
    const bytes = await readExact(
      source,
      format.dataOffset + frame * format.blockAlign,
      frameCount * format.blockAlign,
    );
    if (meter) meter.add(bytes.byteLength, 1);
    try {
      yield bytes;
    } finally {
      if (meter) meter.remove(bytes.byteLength);
    }
  }
}

export async function* streamEffectPcm(source, format, options = {}) {
  const totalFrames = format.frames;
  const region = normalizeRegion(options.region, totalFrames);
  const blockFrames = Math.max(
    1,
    Math.floor(options.blockFrames || DEFAULT_BLOCK_FRAMES),
  );
  const prefetchBlocks = Math.max(
    0,
    Math.floor(options.prefetchBlocks ?? DEFAULT_PREFETCH_BLOCKS),
  );
  const startFrame = Math.max(0, Math.min(totalFrames, Math.floor(options.startFrame || 0)));
  const endFrame = Math.max(
    startFrame,
    Math.min(totalFrames, Math.floor(options.endFrame ?? totalFrames)),
  );
  const meter = options.meter;

  if (startFrame >= endFrame) return;

  const prefixEnd = Math.min(endFrame, region.startFrame);
  if (startFrame < prefixEnd) {
    yield* forwardChunks(source, format, startFrame, prefixEnd, blockFrames, meter);
  }

  const reverseStart = Math.max(startFrame, region.startFrame);
  const reverseEnd = Math.min(endFrame, region.endFrame);
  if (reverseStart < reverseEnd) {
    const prefetcher = new ReverseBlockPrefetcher(
      source,
      format,
      region,
      blockFrames,
      prefetchBlocks,
      meter,
    );
    const regionFrames = region.endFrame - region.startFrame;
    let relativeOutput = reverseStart - region.startFrame;
    const relativeEnd = reverseEnd - region.startFrame;

    try {
      while (relativeOutput < relativeEnd) {
        const sourceFrame = region.endFrame - 1 - relativeOutput;
        const blockIndex = Math.floor(
          (sourceFrame - region.startFrame) / blockFrames,
        );
        const sourceBlockStart = region.startFrame + blockIndex * blockFrames;
        const sourceBlockFrames = Math.min(
          blockFrames,
          region.endFrame - sourceBlockStart,
        );
        const blockOutputStart =
          regionFrames - (sourceBlockStart - region.startFrame + sourceBlockFrames);
        const insideBlock = relativeOutput - blockOutputStart;
        const take = Math.min(
          sourceBlockFrames - insideBlock,
          relativeEnd - relativeOutput,
        );

        const block = await prefetcher.get(blockIndex);
        try {
          const reversed = reverseFrameBlock(
            block.bytes,
            format,
            block.frameCount,
          );
          const byteStart = insideBlock * format.blockAlign;
          const byteEnd = (insideBlock + take) * format.blockAlign;
          yield reversed.subarray(byteStart, byteEnd);
        } finally {
          prefetcher.release(blockIndex);
        }
        relativeOutput += take;
      }
    } finally {
      prefetcher.close();
    }
  }

  const suffixStart = Math.max(startFrame, region.endFrame);
  if (suffixStart < endFrame) {
    yield* forwardChunks(source, format, suffixStart, endFrame, blockFrames, meter);
  }
}

export async function collectStream(stream) {
  const chunks = [];
  let byteLength = 0;
  for await (const chunk of stream) {
    chunks.push(chunk);
    byteLength += chunk.byteLength;
  }
  const output = new Uint8Array(byteLength);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return output;
}

