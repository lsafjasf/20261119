const TYPE_SIZE = { 1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 10: 8 };

function align(value, boundary = 4) {
  return Math.ceil(value / boundary) * boundary;
}

function asciiField(value, capacity) {
  const source = new TextEncoder().encode(value);
  const bytes = new Uint8Array(capacity ?? source.length + 1);
  bytes.set(source.subarray(0, bytes.length - 1));
  return bytes;
}

function encodeNumbers(type, values, littleEndian) {
  const size = TYPE_SIZE[type];
  const bytes = new Uint8Array(values.length * size);
  const view = new DataView(bytes.buffer);
  values.forEach((value, index) => {
    if (type === 1 || type === 7) bytes[index] = value;
    else if (type === 3) view.setUint16(index * 2, value, littleEndian);
    else if (type === 4) view.setUint32(index * 4, value, littleEndian);
    else if (type === 5) {
      view.setUint32(index * 8, value[0], littleEndian);
      view.setUint32(index * 8 + 4, value[1], littleEndian);
    } else if (type === 10) {
      view.setInt32(index * 8, value[0], littleEndian);
      view.setInt32(index * 8 + 4, value[1], littleEndian);
    }
  });
  return bytes;
}

function entryBytes(entry, littleEndian) {
  if (entry.pointer) return null;
  if (entry.data instanceof Uint8Array) return entry.data;
  if (typeof entry.data === "string") return asciiField(entry.data, entry.count);
  const count = entry.count ?? entry.data.length;
  return encodeNumbers(entry.type, entry.data, littleEndian);
}

export function buildTiff({ littleEndian = true, ifds, root = ifds[0].key, entryOverrides = {} }) {
  const ifdOffsets = new Map();
  let cursor = 8;
  for (const ifd of ifds) {
    ifdOffsets.set(ifd.key, cursor);
    cursor += 2 + ifd.entries.length * 12 + 4;
    cursor = align(cursor, 2);
  }
  const externalStart = align(cursor, 4);
  cursor = externalStart;
  const prepared = new Map();

  for (const ifd of ifds) {
    const entries = ifd.entries.map((entry, index) => {
      const data = entryBytes(entry, littleEndian);
      const size = data ? data.length : 4;
      const offsetOverride = entry.valueOffsetOverride ?? entryOverrides[`${ifd.key}:${entry.tag}:${index}`];
      const needsExternal = !entry.pointer && (size > 4 || entry.forceOutOfLine);
      let externalOffset = null;
      if (needsExternal && offsetOverride === undefined) {
        externalOffset = align(cursor, 2);
        cursor = externalOffset + size;
        cursor = align(cursor, 2);
      }
      return { ...entry, index, data, size, externalOffset, offsetOverride };
    });
    prepared.set(ifd.key, entries);
  }
  const totalLength = cursor;
  const bytes = new Uint8Array(totalLength);
  const view = new DataView(bytes.buffer);
  const setU16 = (offset, value) => view.setUint16(offset, value, littleEndian);
  const setU32 = (offset, value) => view.setUint32(offset, value >>> 0, littleEndian);

  bytes[0] = littleEndian ? 0x49 : 0x4d;
  bytes[1] = littleEndian ? 0x49 : 0x4d;
  setU16(2, 42);
  setU32(4, ifdOffsets.get(root));

  for (const ifd of ifds) {
    const ifdOffset = ifdOffsets.get(ifd.key);
    const entries = prepared.get(ifd.key);
    const sorted = [...entries].sort((left, right) => left.tag - right.tag || left.index - right.index);
    setU16(ifdOffset, sorted.length);
    sorted.forEach((entry, index) => {
      const offset = ifdOffset + 2 + index * 12;
      const count = entry.count ?? (entry.pointer ? 1 : entry.data.length);
      setU16(offset, entry.tag);
      setU16(offset + 2, entry.pointer ? 4 : entry.type);
      setU32(offset + 4, count);
      if (entry.pointer) {
        setU32(offset + 8, ifdOffsets.get(entry.pointer));
      } else if (entry.size <= 4 && !entry.forceOutOfLine) {
        bytes.set(entry.data, offset + 8);
      } else if (typeof entry.offsetOverride === "function") {
        setU32(offset + 8, entry.offsetOverride({ totalLength }));
      } else if (entry.offsetOverride !== undefined) {
        setU32(offset + 8, entry.offsetOverride);
      } else {
        setU32(offset + 8, entry.externalOffset);
        bytes.set(entry.data, entry.externalOffset);
      }
    });
    const nextOffset = ifd.next ? ifdOffsets.get(ifd.next) : 0;
    setU32(ifdOffset + 2 + sorted.length * 12, nextOffset);
  }
  return bytes;
}

export function insertExif(baseJpeg, tiffBytes) {
  const header = new TextEncoder().encode("Exif\0\0");
  const payloadLength = header.length + tiffBytes.length;
  const segmentLength = payloadLength + 2;
  if (segmentLength > 0xffff) throw new Error("EXIF payload exceeds APP1 limit");
  const segment = new Uint8Array(2 + segmentLength);
  segment[0] = 0xff;
  segment[1] = 0xe1;
  segment[2] = segmentLength >> 8;
  segment[3] = segmentLength & 0xff;
  segment.set(header, 4);
  segment.set(tiffBytes, 4 + header.length);
  const output = new Uint8Array(baseJpeg.length + segment.length);
  output.set(baseJpeg.subarray(0, 2), 0);
  output.set(segment, 2);
  output.set(baseJpeg.subarray(2), 2 + segment.length);
  return output;
}

const orientation = (value) => ({ tag: 0x0112, type: 3, count: 1, data: [value] });
const exifPointer = (target) => ({ tag: 0x8769, pointer: target });
const gpsPointer = (target) => ({ tag: 0x8825, pointer: target });
const subIfdPointer = (target) => ({ tag: 0x014a, pointer: target });

function baseIfds({ littleEndian, orientationValue, date, make, model, withGps = true, withSub = false }) {
  const ifds = [
    {
      key: "ifd0",
      entries: [
        { tag: 0x010f, type: 2, count: 20, data: asciiField(make, 20) },
        { tag: 0x0110, type: 2, count: 16, data: asciiField(model, 16) },
        orientation(orientationValue),
        { tag: 0x011a, type: 5, count: 1, data: [[72, 1]] },
        { tag: 0x011b, type: 5, count: 1, data: [[72, 1]] },
        { tag: 0x0128, type: 3, count: 1, data: [2] },
        exifPointer("exif"),
        ...(withGps ? [gpsPointer("gps")] : []),
        ...(withSub ? [subIfdPointer("sub")] : []),
      ],
      next: "ifd1",
    },
    {
      key: "exif",
      entries: [
        { tag: 0x9003, type: 2, count: 20, data: asciiField(date, 20) },
        { tag: 0x9004, type: 2, count: 20, data: asciiField(date, 20) },
        { tag: 0x9000, type: 7, count: 4, data: new Uint8Array([0x30, 0x32, 0x33, 0x32]) },
        { tag: 0xa001, type: 3, count: 1, data: [1] },
        { tag: 0xa002, type: 4, count: 1, data: [240] },
        { tag: 0xa003, type: 4, count: 1, data: [150] },
      ],
    },
    {
      key: "ifd1",
      entries: [
        { tag: 0x0103, type: 3, count: 1, data: [6] },
        { tag: 0x011a, type: 5, count: 1, data: [[72, 1]] },
        { tag: 0x011b, type: 5, count: 1, data: [[72, 1]] },
        { tag: 0x0128, type: 3, count: 1, data: [2] },
      ],
    },
  ];
  if (withGps) {
    ifds.push({
      key: "gps",
      entries: [
        { tag: 0x0000, type: 1, count: 4, data: new Uint8Array([2, 3, 0, 0]) },
        { tag: 0x0001, type: 2, count: 2, data: asciiField("N", 2) },
        { tag: 0x0002, type: 5, count: 3, data: [[31, 1], [12, 1], [5600, 100]] },
        { tag: 0x0003, type: 2, count: 2, data: asciiField("E", 2) },
        { tag: 0x0004, type: 5, count: 3, data: [[121, 1], [28, 1], [3800, 100]] },
      ],
    });
  }
  if (withSub) {
    ifds.push({
      key: "sub",
      entries: [
        { tag: 0x0100, type: 4, count: 1, data: [240] },
        { tag: 0x0101, type: 4, count: 1, data: [150] },
        { tag: 0x0103, type: 3, count: 1, data: [6] },
      ],
    });
  }
  return ifds;
}

function duplicateIfds() {
  return [
    {
      key: "ifd0",
      entries: [
        { tag: 0x010f, type: 2, count: 16, data: asciiField("STUDIO ARC", 16) },
        orientation(6),
        orientation(3),
        exifPointer("exif"),
      ],
      next: "ifd1",
    },
    {
      key: "exif",
      entries: [
        { tag: 0x9003, type: 2, count: 20, data: asciiField("2022:07:11 09:00:03", 20) },
        { tag: 0x9003, type: 2, count: 20, data: asciiField("2021:02:03 04:05:06", 20) },
        { tag: 0x9004, type: 2, count: 20, data: asciiField("2022:07:11 09:00:03", 20) },
        { tag: 0xa001, type: 3, count: 1, data: [1] },
      ],
    },
    {
      key: "ifd1",
      entries: [{ tag: 0x0103, type: 3, count: 1, data: [6] }],
    },
  ];
}

function illegalIfds() {
  return [
    {
      key: "ifd0",
      entries: [
        { tag: 0x010f, type: 2, count: 16, data: asciiField("FIELD NOTES", 16) },
        orientation(8),
        exifPointer("exif"),
        gpsPointer("gps"),
      ],
      next: "ifd1",
    },
    {
      key: "exif",
      entries: [
        {
          tag: 0x9003,
          type: 2,
          count: 20,
          data: asciiField("2020:12:01 08:00:00", 20),
          valueOffsetOverride: ({ totalLength }) => totalLength + 48,
        },
        { tag: 0x9000, type: 7, count: 4, data: new Uint8Array([0x30, 0x32, 0x33, 0x32]) },
        { tag: 0xa001, type: 3, count: 1, data: [1] },
      ],
    },
    {
      key: "ifd1",
      entries: [{ tag: 0x0103, type: 3, count: 1, data: [6] }],
    },
    {
      key: "gps",
      entries: [
        { tag: 0x0000, type: 1, count: 4, data: new Uint8Array([2, 3, 0, 0]) },
        { tag: 0x0001, type: 2, count: 2, data: asciiField("S", 2) },
      ],
    },
  ];
}

export function buildSampleSet(baseJpeg) {
  const samples = [
    {
      id: "canon-le",
      name: "Canon EOS · 小端",
      subtitle: "II · 外置日期 · 3 IFD",
      note: "标准小端 TIFF，日期与制造商字段均外置，方向内联。",
      features: ["II", "外置字段", "多 IFD"],
      bytes: insertExif(baseJpeg, buildTiff({
        littleEndian: true,
        ifds: baseIfds({
          littleEndian: true, orientationValue: 6, date: "2023:10:08 16:24:51",
          make: "Canon", model: "EOS R5",
        }),
      })),
    },
    {
      id: "nikon-be",
      name: "Nikon Z8 · 大端",
      subtitle: "MM · 外置日期 · 2 IFD",
      note: "大端 TIFF（MM），同一套偏移计算逻辑，日期槽保持 20 字节。",
      features: ["MM", "外置字段", "大端"],
      bytes: insertExif(baseJpeg, buildTiff({
        littleEndian: false,
        ifds: baseIfds({
          littleEndian: false, orientationValue: 1, date: "2024:03:19 11:07:42",
          make: "Nikon", model: "Z8", withGps: false,
        }),
      })),
    },
    {
      id: "multi-ifd",
      name: "Multi-IFD 扫描件",
      subtitle: "II · Exif/GPS/SubIFD · 4 IFD",
      note: "IFD0 同时引用 Exif、GPS 与 SubIFD，指针对均相对 TIFF 头计算。",
      features: ["II", "4 IFD", "指针链"],
      bytes: insertExif(baseJpeg, buildTiff({
        littleEndian: true,
        ifds: baseIfds({
          littleEndian: true, orientationValue: 8, date: "1998:06:14 07:15:02",
          make: "Phase One", model: "P45+", withSub: true,
        }),
      })),
    },
    {
      id: "duplicate",
      name: "重复标签样本",
      subtitle: "II · 2×日期 · 2×方向",
      note: "同一 IFD 内各出现两次 DateTimeOriginal 与 Orientation，写入时同步全部副本。",
      features: ["II", "重复 ×4", "冲突值"],
      bytes: insertExif(baseJpeg, buildTiff({ littleEndian: true, ifds: duplicateIfds() })),
    },
    {
      id: "illegal-offset",
      name: "非法偏移样本",
      subtitle: "MM · 日期引用越界",
      note: "DateTimeOriginal 值偏移落在 TIFF 之外。工具应展示引用并拒绝任何写出。",
      features: ["MM", "越界", "只读隔离"],
      quarantine: true,
      bytes: insertExif(baseJpeg, buildTiff({ littleEndian: false, ifds: illegalIfds() })),
    },
  ];
  return samples.map((sample) => ({
    ...sample,
    fileName: `${sample.id}.jpg`,
    size: sample.bytes.length,
  }));
}
