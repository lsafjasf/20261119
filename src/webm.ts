export interface WebMTrackInfo {
  number: number | null;
  type: "video" | "audio" | "unknown";
  codecId: string;
  width: number | null;
  height: number | null;
  channels: number | null;
  sampleRate: number | null;
}

export interface WebMInspection {
  container: "webm";
  durationMs: number | null;
  timecodeScale: number;
  video: WebMTrackInfo | null;
  audio: WebMTrackInfo | null;
  tracks: WebMTrackInfo[];
  videoFrames: number;
  audioBlocks: number;
  maxBlockTimestampMs: number | null;
  warnings: string[];
}

interface EbmlElement {
  id: number;
  dataStart: number;
  dataEnd: number;
  unknownSize: boolean;
}

const ID = {
  segment: 0x18538067,
  info: 0x1549a966,
  timecodeScale: 0x2ad7b1,
  duration: 0x4489,
  tracks: 0x1654ae6b,
  trackEntry: 0xae,
  trackNumber: 0xd7,
  trackType: 0x83,
  codecId: 0x86,
  video: 0xe0,
  pixelWidth: 0xb0,
  pixelHeight: 0xba,
  audio: 0xe1,
  channels: 0x9f,
  samplingFrequency: 0xb5,
  cluster: 0x1f43b675,
  timecode: 0xe7,
  simpleBlock: 0xa3,
  blockGroup: 0xa0,
  block: 0xa1,
  blockDuration: 0x9b,
} as const;

function readElementId(view: DataView, offset: number, end: number): { id: number; next: number } {
  if (offset >= end) {
    throw new RangeError("EBML ID is out of bounds");
  }
  const first = view.getUint8(offset);
  let length = 1;
  let mask = 0x80;
  while (length <= 4 && (first & mask) === 0) {
    length += 1;
    mask >>= 1;
  }
  if (length > 4 || offset + length > end) {
    throw new RangeError("Invalid EBML ID");
  }
  let id = 0;
  for (let index = 0; index < length; index += 1) {
    id = id * 256 + view.getUint8(offset + index);
  }
  return { id, next: offset + length };
}

function readVint(
  view: DataView,
  offset: number,
  end: number,
): { value: number; length: number; unknown: boolean } {
  if (offset >= end) {
    throw new RangeError("EBML variable integer is out of bounds");
  }
  const first = view.getUint8(offset);
  let length = 1;
  let mask = 0x80;
  while (length <= 8 && (first & mask) === 0) {
    length += 1;
    mask >>= 1;
  }
  if (length > 8 || offset + length > end) {
    throw new RangeError("Invalid EBML variable integer");
  }

  let value = BigInt(first & (mask - 1));
  for (let index = 1; index < length; index += 1) {
    const byte = view.getUint8(offset + index);
    value = (value << 8n) | BigInt(byte);
  }
  const allOnes = (1n << BigInt(length * 7)) - 1n;
  const unknown = value === allOnes;
  if (!unknown && value > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new RangeError("EBML size exceeds the safe integer range");
  }
  return { value: unknown ? 0 : Number(value), length, unknown };
}

function readElement(view: DataView, offset: number, parentEnd: number): EbmlElement {
  const id = readElementId(view, offset, parentEnd);
  const size = readVint(view, id.next, parentEnd);
  const dataStart = id.next + size.length;
  const dataEnd = size.unknown ? parentEnd : dataStart + size.value;
  if (dataStart > parentEnd || dataEnd > parentEnd) {
    throw new RangeError("EBML element exceeds its parent");
  }
  return {
    id: id.id,
    dataStart,
    dataEnd,
    unknownSize: size.unknown,
  };
}

function children(view: DataView, start: number, end: number): EbmlElement[] {
  const result: EbmlElement[] = [];
  let offset = start;
  while (offset < end) {
    const element = readElement(view, offset, end);
    result.push(element);
    offset = element.dataEnd;
    if (offset === element.dataStart && element.unknownSize) {
      break;
    }
  }
  return result;
}

function childrenTolerant(view: DataView, start: number, end: number): EbmlElement[] {
  const result: EbmlElement[] = [];
  let offset = start;
  while (offset < end) {
    try {
      const element = readElement(view, offset, end);
      if (element.dataEnd <= element.dataStart) break;
      result.push(element);
      offset = element.dataEnd;
    } catch {
      break;
    }
  }
  return result;
}

function findNextCluster(view: DataView, start: number, end: number): number | null {
  let offset = start;
  while (offset < end) {
    try {
      const element = readElement(view, offset, end);
      if (element.id === ID.cluster) return offset;
      offset = element.dataEnd;
    } catch {
      return null;
    }
  }
  return null;
}

function segmentChildren(view: DataView, start: number, end: number): EbmlElement[] {
  const result: EbmlElement[] = [];
  let offset = start;
  while (offset < end) {
    const element = readElement(view, offset, end);
    if (element.unknownSize && element.id === ID.cluster) {
      const nextCluster = findNextCluster(view, element.dataStart, end);
      element.dataEnd = nextCluster ?? end;
    }
    result.push(element);
    offset = element.dataEnd;
    if (offset <= element.dataStart) break;
  }
  return result;
}

function readUnsigned(view: DataView, element: EbmlElement): number {
  let value = 0;
  for (let offset = element.dataStart; offset < element.dataEnd; offset += 1) {
    value = value * 256 + view.getUint8(offset);
  }
  return value;
}

function readFloat(view: DataView, element: EbmlElement): number {
  const size = element.dataEnd - element.dataStart;
  if (size === 4) {
    return view.getFloat32(element.dataStart);
  }
  if (size === 8) {
    return view.getFloat64(element.dataStart);
  }
  return Number.NaN;
}

function readString(view: DataView, element: EbmlElement): string {
  const bytes = new Uint8Array(view.buffer, view.byteOffset + element.dataStart, element.dataEnd - element.dataStart);
  return new TextDecoder().decode(bytes).replace(/\0+$/g, "");
}

function childValue(view: DataView, parent: EbmlElement, wantedId: number): EbmlElement | null {
  return children(view, parent.dataStart, parent.dataEnd).find((element) => element.id === wantedId) ?? null;
}

function parseBlock(view: DataView, element: EbmlElement): { trackNumber: number; relativeTimecode: number } | null {
  if (element.dataEnd - element.dataStart < 4) {
    return null;
  }
  const track = readVint(view, element.dataStart, element.dataEnd);
  const timecodeOffset = element.dataStart + track.length;
  if (timecodeOffset + 2 > element.dataEnd) {
    return null;
  }
  const relativeTimecode = view.getInt16(timecodeOffset);
  return { trackNumber: track.value, relativeTimecode };
}

function emptyTrack(): WebMTrackInfo {
  return {
    number: null,
    type: "unknown",
    codecId: "",
    width: null,
    height: null,
    channels: null,
    sampleRate: null,
  };
}

function parseTrack(view: DataView, entry: EbmlElement): WebMTrackInfo {
  const track = emptyTrack();
  const number = childValue(view, entry, ID.trackNumber);
  const type = childValue(view, entry, ID.trackType);
  const codec = childValue(view, entry, ID.codecId);
  if (number) track.number = readUnsigned(view, number);
  if (codec) track.codecId = readString(view, codec);
  if (type) {
    const trackType = readUnsigned(view, type);
    track.type = trackType === 1 ? "video" : trackType === 2 ? "audio" : "unknown";
  }

  const video = childValue(view, entry, ID.video);
  if (video) {
    const width = childValue(view, video, ID.pixelWidth);
    const height = childValue(view, video, ID.pixelHeight);
    if (width) track.width = readUnsigned(view, width);
    if (height) track.height = readUnsigned(view, height);
  }

  const audio = childValue(view, entry, ID.audio);
  if (audio) {
    const channels = childValue(view, audio, ID.channels);
    const sampleRate = childValue(view, audio, ID.samplingFrequency);
    if (channels) track.channels = readUnsigned(view, channels);
    if (sampleRate) track.sampleRate = readFloat(view, sampleRate);
  }
  return track;
}

function readSegment(view: DataView, segment: EbmlElement): WebMInspection {
  const inspection: WebMInspection = {
    container: "webm",
    durationMs: null,
    timecodeScale: 1_000_000,
    video: null,
    audio: null,
    tracks: [],
    videoFrames: 0,
    audioBlocks: 0,
    maxBlockTimestampMs: null,
    warnings: [],
  };

  const segmentElements = segmentChildren(view, segment.dataStart, segment.dataEnd);
  const info = segmentElements.find((element) => element.id === ID.info);
  if (info) {
    const scale = childValue(view, info, ID.timecodeScale);
    if (scale) inspection.timecodeScale = readUnsigned(view, scale) || 1_000_000;
    const duration = childValue(view, info, ID.duration);
    if (duration) {
      const rawDuration = readFloat(view, duration);
      if (Number.isFinite(rawDuration) && rawDuration > 0) {
        inspection.durationMs = (rawDuration * inspection.timecodeScale) / 1_000_000;
      }
    }
  }

  const tracks = segmentElements.find((element) => element.id === ID.tracks);
  if (tracks) {
    for (const entry of children(view, tracks.dataStart, tracks.dataEnd)) {
      if (entry.id === ID.trackEntry) {
        inspection.tracks.push(parseTrack(view, entry));
      }
    }
  }
  inspection.video = inspection.tracks.find((track) => track.type === "video") ?? null;
  inspection.audio = inspection.tracks.find((track) => track.type === "audio") ?? null;

  const trackTypes = new Map<number, WebMTrackInfo["type"]>();
  for (const track of inspection.tracks) {
    if (track.number !== null) trackTypes.set(track.number, track.type);
  }

  for (const cluster of segmentElements.filter((element) => element.id === ID.cluster)) {
    const clusterChildren = childrenTolerant(view, cluster.dataStart, cluster.dataEnd);
    const timecodeElement = clusterChildren.find((element) => element.id === ID.timecode);
    const clusterTimecode = timecodeElement ? readUnsigned(view, timecodeElement) : 0;

    for (const child of clusterChildren) {
      let blockElement: EbmlElement | null = null;
      let blockDuration = 0;
      if (child.id === ID.simpleBlock) {
        blockElement = child;
      } else if (child.id === ID.blockGroup) {
        const groupChildren = childrenTolerant(view, child.dataStart, child.dataEnd);
        blockElement = groupChildren.find((element) => element.id === ID.block) ?? null;
        const durationElement = groupChildren.find((element) => element.id === ID.blockDuration);
        if (durationElement) blockDuration = readUnsigned(view, durationElement);
      }
      if (!blockElement) continue;

      const block = parseBlock(view, blockElement);
      if (!block) continue;
      const blockTimestampMs =
        ((clusterTimecode + block.relativeTimecode) * inspection.timecodeScale) / 1_000_000;
      const endTimestampMs =
        ((clusterTimecode + block.relativeTimecode + blockDuration) * inspection.timecodeScale) /
        1_000_000;
      inspection.maxBlockTimestampMs = Math.max(
        inspection.maxBlockTimestampMs ?? 0,
        blockTimestampMs,
        endTimestampMs,
      );
      const trackType = trackTypes.get(block.trackNumber);
      if (trackType === "video") inspection.videoFrames += 1;
      if (trackType === "audio") inspection.audioBlocks += 1;
    }
  }

  if (inspection.durationMs === null) {
    inspection.warnings.push("WebM 容器没有写入 Duration，已改用最后一个数据块的时间戳估算时长。");
  }
  if (!inspection.video) inspection.warnings.push("未找到视频轨。");
  if (!inspection.audio) inspection.warnings.push("未找到音频轨。");
  return inspection;
}

export function inspectWebM(buffer: ArrayBuffer): WebMInspection {
  const view = new DataView(buffer);
  const root = children(view, 0, buffer.byteLength);
  const segment = root.find((element) => element.id === ID.segment);
  if (!segment) {
    throw new Error("文件不是有效的 WebM：缺少 Segment 元素。");
  }
  return readSegment(view, segment);
}

export function measuredDurationMs(inspection: WebMInspection): number | null {
  const blockDuration = inspection.maxBlockTimestampMs;
  if (inspection.durationMs !== null && blockDuration !== null) {
    return Math.max(inspection.durationMs, blockDuration);
  }
  return inspection.durationMs ?? blockDuration;
}
