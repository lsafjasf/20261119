import { sha256 } from "./sha256.js";

export const TYPE_SIZES = {
  1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8,
};

export const TYPE_NAMES = {
  1: "BYTE", 2: "ASCII", 3: "SHORT", 4: "LONG", 5: "RATIONAL",
  6: "SBYTE", 7: "UNDEFINED", 8: "SSHORT", 9: "SLONG", 10: "SRATIONAL",
  11: "FLOAT", 12: "DOUBLE",
};

export const TAG_NAMES = {
  0x010e: "ImageDescription",
  0x010f: "Make",
  0x0110: "Model",
  0x0112: "Orientation",
  0x011a: "XResolution",
  0x011b: "YResolution",
  0x0128: "ResolutionUnit",
  0x0131: "Software",
  0x0132: "DateTime",
  0x014a: "SubIFDs",
  0x0201: "JPEGInterchangeFormat",
  0x0202: "JPEGInterchangeFormatLength",
  0x8769: "ExifIFDPointer",
  0x8825: "GPSInfoIFDPointer",
  0x9000: "ExifVersion",
  0x9003: "DateTimeOriginal",
  0x9004: "DateTimeDigitized",
  0x9101: "ComponentsConfiguration",
  0xa001: "ColorSpace",
  0xa002: "PixelXDimension",
  0xa003: "PixelYDimension",
  0xa005: "InteroperabilityIFDPointer",
  0x0001: "GPSLatitudeRef",
  0x0002: "GPSLatitude",
  0x0003: "GPSLongitudeRef",
  0x0004: "GPSLongitude",
  0x0005: "GPSAltitudeRef",
  0x0006: "GPSAltitude",
};

export const TARGET_TAGS = {
  date: 0x9003,
  orientation: 0x0112,
};

const POINTER_TAGS = new Set([0x014a, 0x8769, 0x8825, 0xa005]);
const MARKER_NAMES = {
  0xc0: "SOF0", 0xc1: "SOF1", 0xc2: "SOF2", 0xc3: "SOF3", 0xc5: "SOF5",
  0xc6: "SOF6", 0xc7: "SOF7", 0xc9: "SOF9", 0xca: "SOF10", 0xcb: "SOF11",
  0xcd: "SOF13", 0xce: "SOF14", 0xcf: "SOF15", 0xc4: "DHT", 0xcc: "DAC",
  0xd8: "SOI", 0xd9: "EOI", 0xda: "SOS", 0xdb: "DQT", 0xdc: "DNL",
  0xdd: "DRI", 0xde: "DHP", 0xdf: "EXP", 0xe0: "APP0", 0xe1: "APP1",
  0xe2: "APP2", 0xe3: "APP3", 0xe4: "APP4", 0xe5: "APP5", 0xe6: "APP6",
  0xe7: "APP7", 0xe8: "APP8", 0xe9: "APP9", 0xea: "APP10", 0xeb: "APP11",
  0xec: "APP12", 0xed: "APP13", 0xee: "APP14", 0xef: "APP15", 0xfe: "COM",
};

export const hex = (value, width = 4) => value.toString(16).toUpperCase().padStart(width, "0");
export const offsetLabel = (value) => `0x${hex(value, 6)}`;
export const bytesToHex = (bytes, separator = " ") =>
  Array.from(bytes, (byte) => hex(byte, 2)).join(separator);

function ascii(bytes, start, length) {
  let result = "";
  for (let index = 0; index < length; index += 1) {
    result += String.fromCharCode(bytes[start + index]);
  }
  return result;
}

export function parseJpeg(input) {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  const issues = [];
  const segments = [];
  const addIssue = (severity, code, message, extra = {}) => {
    issues.push({ severity, code, message, ...extra });
  };

  if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8) {
    addIssue("error", "not-jpeg", "文件缺少 JPEG SOI 标记");
    return {
      valid: false, bytes, fileSize: bytes.length, issues, segments,
      exif: null, scan: null, frame: null,
    };
  }

  segments.push({ marker: 0xd8, name: "SOI", start: 0, end: 2, payloadStart: 2, payloadEnd: 2 });
  let position = 2;
  let scan = null;
  let exif = null;
  let frame = null;
  let exifSegmentCount = 0;

  while (position < bytes.length) {
    if (bytes[position] !== 0xff) {
      addIssue("error", "marker-desync", `在 ${offsetLabel(position)} 处未找到 JPEG 标记`, { offset: position });
      break;
    }
    const markerStart = position;
    while (bytes[position] === 0xff) position += 1;
    if (position >= bytes.length) {
      addIssue("error", "truncated-marker", "文件在 JPEG 标记处截断", { offset: markerStart });
      break;
    }
    const marker = bytes[position];
    const markerEnd = position + 1;
    const name = MARKER_NAMES[marker] || (marker >= 0xd0 && marker <= 0xd7 ? `RST${marker - 0xd0}` : `0x${hex(marker, 2)}`);

    if (marker === 0xd9) {
      segments.push({ marker, name: "EOI", start: markerStart, end: markerEnd, payloadStart: markerEnd, payloadEnd: markerEnd });
      break;
    }
    if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) {
      segments.push({ marker, name, start: markerStart, end: markerEnd, payloadStart: markerEnd, payloadEnd: markerEnd });
      position = markerEnd;
      continue;
    }
    if (markerEnd + 2 > bytes.length) {
      addIssue("error", "truncated-segment", `${name} 段长度字段被截断`, { offset: markerStart });
      break;
    }

    const payloadLength = (bytes[markerEnd] << 8) | bytes[markerEnd + 1];
    const payloadStart = markerEnd + 2;
    const payloadEnd = payloadStart + payloadLength - 2;
    if (payloadLength < 2 || payloadEnd > bytes.length) {
      addIssue("error", "segment-oob", `${name} 段超出文件边界`, { offset: markerStart });
      break;
    }

    const segment = {
      marker, name, start: markerStart, end: payloadEnd,
      lengthField: markerEnd, payloadLength, payloadStart, payloadEnd,
    };

    if ((marker >= 0xc0 && marker <= 0xcf) && ![0xc4, 0xc8, 0xcc].includes(marker) && payloadEnd - payloadStart >= 5) {
      frame = {
        width: (bytes[payloadStart + 3] << 8) | bytes[payloadStart + 4],
        height: (bytes[payloadStart + 1] << 8) | bytes[payloadStart + 2],
        precision: bytes[payloadStart],
        components: bytes[payloadStart + 5],
      };
    }

    if (marker === 0xe1 && payloadLength >= 8 && ascii(bytes, payloadStart, 6) === "Exif\0\0") {
      exifSegmentCount += 1;
      segment.exif = true;
      if (!exif) {
        exif = parseTiff(bytes, payloadStart + 6, payloadEnd, segment, exifSegmentCount);
      } else {
        addIssue("warning", "multiple-exif", "发现多个 EXIF APP1，当前只编辑第一个", { offset: markerStart });
      }
    }

    if (marker === 0xda) {
      const scanStart = payloadEnd;
      const scanEnd = findScanEnd(bytes, scanStart);
      segment.scanStart = scanStart;
      segment.scanEnd = scanEnd.dataEnd;
      segment.headerEnd = payloadEnd;
      segment.end = scanEnd.dataEnd;
      segments.push(segment);
      scan = {
        start: scanStart,
        end: scanEnd.dataEnd,
        eoiStart: scanEnd.eoiStart,
        eoiEnd: scanEnd.eoiEnd,
        length: scanEnd.dataEnd - scanStart,
        hash: sha256(bytes.subarray(scanStart, scanEnd.dataEnd)),
      };
      if (scanEnd.error) addIssue("error", scanEnd.error, "无法定位扫描数据结束标记", { offset: scanStart });
      if (scanEnd.eoiStart !== null) {
        segments.push({
          marker: 0xd9, name: "EOI", start: scanEnd.eoiStart, end: scanEnd.eoiEnd,
          payloadStart: scanEnd.eoiEnd, payloadEnd: scanEnd.eoiEnd,
        });
      }
      position = scanEnd.eoiEnd ?? bytes.length;
      if (position < bytes.length) {
        addIssue("warning", "trailing-bytes", "EOI 之后存在附加字节，未纳入扫描哈希", { offset: position });
      }
      break;
    }

    segments.push(segment);
    position = payloadEnd;
  }

  if (!scan) addIssue("error", "missing-scan", "未找到 SOS 扫描段");
  if (!exif) addIssue("error", "missing-exif", "未找到 Exif APP1 / TIFF 元数据");
  if (exifSegmentCount > 1) addIssue("warning", "multiple-exif", `检测到 ${exifSegmentCount} 个 EXIF APP1 段`);
  if (exif) {
    for (const issue of exif.issues) issues.push(issue);
  }
  return {
    valid: !issues.some((issue) => issue.severity === "error"),
    bytes,
    fileSize: bytes.length,
    segments,
    frame,
    exif,
    scan,
    issues,
  };
}

function findScanEnd(bytes, start) {
  let position = start;
  while (position < bytes.length - 1) {
    if (bytes[position] !== 0xff) {
      position += 1;
      continue;
    }
    let marker = position + 1;
    while (marker < bytes.length && bytes[marker] === 0xff) marker += 1;
    if (marker >= bytes.length) break;
    const code = bytes[marker];
    if (code === 0x00 || (code >= 0xd0 && code <= 0xd7)) {
      position = marker + 1;
      continue;
    }
    if (code === 0xd9) {
      return { dataEnd: position, eoiStart: position, eoiEnd: marker + 1 };
    }
    return { dataEnd: position, eoiStart: null, eoiEnd: null, error: "scan-unknown-marker" };
  }
  return { dataEnd: bytes.length, eoiStart: null, eoiEnd: null, error: "missing-eoi" };
}

function parseTiff(bytes, tiffStart, tiffEnd, segment, segmentNumber) {
  const tiffLength = tiffEnd - tiffStart;
  const exif = {
    segmentNumber,
    segmentStart: segment.start,
    segmentEnd: segment.end,
    tiffStart,
    tiffEnd,
    tiffLength,
    byteOrder: null,
    ifd0Offset: null,
    ifds: [],
    entries: [],
    references: [],
    duplicateTags: [],
    issues: [],
  };
  const addIssue = (severity, code, message, relOffset = null, extra = {}) => {
    exif.issues.push({
      severity,
      code,
      message,
      offset: relOffset === null ? null : tiffStart + relOffset,
      relOffset,
      ...extra,
    });
  };
  const inBounds = (offset, length) =>
    Number.isInteger(offset) && Number.isInteger(length) &&
    offset >= 0 && length >= 0 && offset + length <= tiffLength;

  if (tiffLength < 8) {
    addIssue("error", "tiff-truncated", "Exif APP1 中 TIFF 头不足 8 字节", 0);
    return exif;
  }

  const order = ascii(bytes, tiffStart, 2);
  if (order !== "II" && order !== "MM") {
    addIssue("error", "bad-byte-order", `无效 TIFF 字节序标记 "${order}"`, 0);
    return exif;
  }
  exif.byteOrder = order === "II" ? "little" : "big";
  const little = exif.byteOrder === "little";
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const u16 = (rel) => view.getUint16(tiffStart + rel, little);
  const u32 = (rel) => view.getUint32(tiffStart + rel, little);
  const i16 = (rel) => view.getInt16(tiffStart + rel, little);
  const i32 = (rel) => view.getInt32(tiffStart + rel, little);

  if (u16(2) !== 42) {
    addIssue("error", "bad-tiff-magic", `TIFF magic 应为 42，实际为 ${u16(2)}`, 2);
    return exif;
  }
  exif.ifd0Offset = u32(4);
  if (!inBounds(exif.ifd0Offset, 2)) {
    addIssue("error", "ifd0-oob", `IFD0 偏移 ${offsetLabel(exif.ifd0Offset)} 超出 TIFF 边界`, 4);
    return exif;
  }

  const ifdByOffset = new Map();
  let rootCount = 1;

  function readNumeric(type, count, dataRel) {
    const numbers = [];
    for (let index = 0; index < count; index += 1) {
      let cursor = dataRel;
      if (type === 1 || type === 6 || type === 7) {
        cursor += index;
        numbers.push(type === 6 ? view.getInt8(tiffStart + cursor) : view.getUint8(tiffStart + cursor));
      } else if (type === 3) {
        cursor += index * 2;
        numbers.push(view.getUint16(tiffStart + cursor, little));
      } else if (type === 8) {
        cursor += index * 2;
        numbers.push(i16(cursor));
      } else if (type === 4) {
        cursor += index * 4;
        numbers.push(u32(cursor));
      } else if (type === 9) {
        cursor += index * 4;
        numbers.push(i32(cursor));
      } else if (type === 5 || type === 10) {
        cursor += index * 8;
        const numerator = type === 5 ? u32(cursor) : i32(cursor);
        const denominator = type === 5 ? u32(cursor + 4) : i32(cursor + 4);
        numbers.push([numerator, denominator]);
      } else if (type === 11) {
        cursor += index * 4;
        numbers.push(view.getFloat32(tiffStart + cursor, little));
      } else if (type === 12) {
        cursor += index * 8;
        numbers.push(view.getFloat64(tiffStart + cursor, little));
      }
    }
    return numbers;
  }

  function decodeEntry(entry, dataRel) {
    const { type, count, dataSize } = entry;
    if (!entry.valid) return;
    entry.rawBytes = bytes.slice(tiffStart + dataRel, tiffStart + dataRel + Math.min(dataSize, 64));
    if (type === 2) {
      let text = "";
      let cursor = dataRel;
      for (let index = 0; index < count; index += 1) {
        const value = bytes[tiffStart + cursor];
        cursor += 1;
        if (value === 0) break;
        text += String.fromCharCode(value);
      }
      entry.ascii = text;
      entry.value = text;
      entry.valueText = JSON.stringify(text);
      return;
    }
    if ([1, 3, 4, 6, 8, 9, 11].includes(type)) {
      entry.numbers = readNumeric(type, count, dataRel);
      entry.value = entry.numbers.length === 1 ? entry.numbers[0] : entry.numbers;
      entry.valueText = formatNumericValues(entry.numbers);
      return;
    }
    if (type === 5 || type === 10) {
      entry.numbers = readNumeric(type, count, dataRel);
      entry.value = entry.numbers;
      entry.valueText = entry.numbers.map(([numerator, denominator]) =>
        denominator === 0 ? `${numerator}/0` : `${numerator}/${denominator}`).join(", ");
      return;
    }
    if (type === 7) {
      entry.rawBytes = bytes.slice(tiffStart + dataRel, tiffStart + dataRel + Math.min(dataSize, 64));
      entry.numbers = Array.from(entry.rawBytes);
      entry.value = entry.rawBytes;
      entry.valueText = bytesToHex(entry.rawBytes);
      return;
    }
    entry.valueText = bytesToHex(entry.rawBytes);
  }

  function parseEntry(ifd, entryRel, index) {
    const tag = u16(entryRel);
    const type = u16(entryRel + 2);
    const count = u32(entryRel + 4);
    const typeSize = TYPE_SIZES[type];
    const entryFileOffset = tiffStart + entryRel;
    const entry = {
      id: `${ifd.id}-e${index}`,
      ifdId: ifd.id,
      ifdPath: ifd.path,
      index,
      tag,
      tagLabel: TAG_NAMES[tag] || `Tag 0x${hex(tag)}`,
      type,
      typeName: TYPE_NAMES[type] || `TYPE_${type}`,
      count,
      entryOffset: entryRel,
      entryFileOffset,
      valueFieldOffset: entryRel + 8,
      valueFieldFileOffset: entryFileOffset + 8,
      dataSize: null,
      storage: null,
      dataOffset: null,
      dataFileOffset: null,
      valid: true,
      issues: [],
    };

    if (!typeSize) {
      entry.valid = false;
      entry.storage = "unknown";
      addIssue("error", "unknown-type", `${entry.tagLabel} 使用未知 TIFF 类型 ${type}`, entryRel, {
        ifdId: ifd.id,
        entryId: entry.id,
      });
      ifd.entries.push(entry);
      exif.entries.push(entry);
      return entry;
    }

    const dataSize = count * typeSize;
    entry.dataSize = dataSize;
    const inline = dataSize <= 4;
    entry.storage = inline ? "inline" : "offset";
    if (inline) {
      entry.dataOffset = entryRel + 8;
      entry.dataFileOffset = entryFileOffset + 8;
      entry.writable = { start: entry.dataFileOffset, end: entry.dataFileOffset + dataSize };
    } else {
      const pointer = u32(entryRel + 8);
      entry.valueOffset = pointer;
      entry.dataOffset = pointer;
      entry.dataFileOffset = tiffStart + pointer;
      entry.writable = { start: entry.dataFileOffset, end: entry.dataFileOffset + dataSize };
      if (!inBounds(pointer, dataSize)) {
        entry.valid = false;
        const end = pointer + dataSize;
        addIssue(
          "error",
          "data-oob",
          `${entry.tagLabel} 指向 ${offsetLabel(pointer)}–${offsetLabel(end)}，超出 TIFF ${offsetLabel(0)}–${offsetLabel(tiffLength)}`,
          entryRel + 8,
          { ifdId: ifd.id, entryId: entry.id, valueOffset: pointer, requiredLength: dataSize },
        );
      }
      exif.references.push({
        kind: "data",
        ifdId: ifd.id,
        entryId: entry.id,
        fromOffset: entryFileOffset + 8,
        toOffset: entry.dataFileOffset,
        length: dataSize,
        valid: entry.valid,
        label: `${entry.tagLabel} 外置字段`,
      });
    }

    if (entry.valid) decodeEntry(entry, entry.dataOffset);
    ifd.entries.push(entry);
    exif.entries.push(entry);

    return entry;
  }

  function pointerKind(tag) {
    if (tag === 0x8769) return "Exif";
    if (tag === 0x8825) return "GPS";
    if (tag === 0xa005) return "Interop";
    return "SubIFD";
  }

  function parseIfdAt(ifdOffset, meta) {
    if (ifdOffset === 0) return null;
    if (ifdByOffset.has(ifdOffset)) {
      return ifdByOffset.get(ifdOffset);
    }
    if (!inBounds(ifdOffset, 2)) {
      addIssue("error", "ifd-oob", `${meta.path} 偏移 ${offsetLabel(ifdOffset)} 超出 TIFF 边界`, null);
      return null;
    }

    const ifd = {
      id: `ifd-${exif.ifds.length}`,
      path: meta.path,
      kind: meta.kind || "child",
      offset: ifdOffset,
      fileOffset: tiffStart + ifdOffset,
      entryCount: 0,
      entries: [],
      nextOffset: 0,
      tableStart: tiffStart + ifdOffset,
      tableEnd: null,
      valid: true,
    };
    ifdByOffset.set(ifdOffset, ifd);
    exif.ifds.push(ifd);

    const count = u16(ifdOffset);
    ifd.entryCount = count;
    if (count > 4096) addIssue("warning", "large-ifd", `${ifd.path} 含 ${count} 个条目，可能异常`, ifdOffset);
    const entriesEnd = ifdOffset + 2 + count * 12;
    if (entriesEnd + 4 > tiffLength) {
      ifd.valid = false;
      addIssue("error", "ifd-table-oob", `${ifd.path} 条目表超出 TIFF 边界`, ifdOffset, { ifdId: ifd.id });
    }
    const readableCount = Math.max(0, Math.min(count, Math.floor((tiffLength - ifdOffset - 2) / 12)));
    for (let index = 0; index < readableCount; index += 1) {
      parseEntry(ifd, ifdOffset + 2 + index * 12, index);
    }
    if (entriesEnd + 4 <= tiffLength) {
      ifd.nextOffset = u32(entriesEnd);
      ifd.tableEnd = tiffStart + entriesEnd + 4;
    } else {
      ifd.tableEnd = Math.min(tiffEnd, tiffStart + entriesEnd);
    }

    for (const duplicate of findDuplicateTags(ifd)) {
      exif.duplicateTags.push(duplicate);
    }

    for (const entry of ifd.entries) {
      if (!POINTER_TAGS.has(entry.tag) || !entry.valid) continue;
      const targets = Array.isArray(entry.numbers) ? entry.numbers : [];
      targets.forEach((target, targetIndex) => {
        if (!Number.isInteger(target) || target === 0) return;
        const childPath = targetIndex === 0
          ? `${ifd.path}.${pointerKind(entry.tag)}`
          : `${ifd.path}.${pointerKind(entry.tag)}${targetIndex}`;
        const relation = {
          kind: "pointer",
          ifdId: ifd.id,
          entryId: entry.id,
          fromOffset: entry.entryFileOffset,
          toOffset: tiffStart + target,
          label: `${entry.tagLabel} → ${childPath}`,
          valid: inBounds(target, 2),
        };
        if (!inBounds(target, 2)) {
          addIssue(
            "error",
            "ifd-pointer-oob",
            `${ifd.path}.${entry.tagLabel} 指向 ${offsetLabel(target)}，超出 TIFF 边界`,
            entry.entryOffset,
            { ifdId: ifd.id, entryId: entry.id, targetOffset: target },
          );
          exif.references.push(relation);
          return;
        }
        const child = parseIfdAt(target, {
          path: childPath,
          kind: pointerKind(entry.tag).toLowerCase(),
          relation,
        });
        if (child) relation.targetIfdId = child.id;
        exif.references.push(relation);
      });
    }

    if (ifd.nextOffset !== 0) {
      const rootNext = meta.rootIndex !== null && meta.rootIndex !== undefined;
      const nextPath = rootNext
        ? `IFD${rootCount++}`
        : `${ifd.path}.next`;
      const relation = {
        kind: "next",
        ifdId: ifd.id,
        entryId: null,
        fromOffset: tiffStart + entriesEnd,
        toOffset: tiffStart + ifd.nextOffset,
        label: `${ifd.path} next → ${nextPath}`,
        valid: inBounds(ifd.nextOffset, 2),
      };
      if (!inBounds(ifd.nextOffset, 2)) {
        addIssue(
          "error",
          "next-ifd-oob",
          `${ifd.path} 的 next IFD 指向 ${offsetLabel(ifd.nextOffset)}，超出 TIFF 边界`,
          entriesEnd,
          { ifdId: ifd.id },
        );
      } else {
        const next = parseIfdAt(ifd.nextOffset, {
          path: nextPath,
          kind: rootNext ? "thumbnail" : "child",
          relation,
          rootIndex: rootNext ? rootCount - 1 : null,
        });
        if (next) relation.targetIfdId = next.id;
      }
      exif.references.push(relation);
    }

    return ifd;
  }

  function findDuplicateTags(ifd) {
    const groups = new Map();
    for (const entry of ifd.entries) {
      if (!groups.has(entry.tag)) groups.set(entry.tag, []);
      groups.get(entry.tag).push(entry);
    }
    const duplicates = [];
    for (const [tag, entries] of groups) {
      if (entries.length < 2) continue;
      for (const entry of entries) entry.duplicateInIfd = true;
      duplicates.push({
        tag,
        tagLabel: TAG_NAMES[tag] || `Tag 0x${hex(tag)}`,
        ifdId: ifd.id,
        ifdPath: ifd.path,
        entryIds: entries.map((entry) => entry.id),
        values: entries.map((entry) => entry.valueText),
      });
    }
    return duplicates;
  }

  parseIfdAt(exif.ifd0Offset, { path: "IFD0", kind: "primary", relation: null, rootIndex: 0 });
  return exif;
}

function formatNumericValues(values) {
  if (values.length === 0) return "∅";
  const visible = values.slice(0, 8).map((value) => String(value));
  if (values.length > 8) visible.push(`… +${values.length - 8}`);
  return visible.join(", ");
}

export function normalizeExifDate(input) {
  const source = String(input ?? "").trim();
  const compact = source.replace(/-/g, ":").replace("T", " ");
  const match = /^(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2})(?::(\d{2}))?$/.exec(compact);
  if (!match) {
    return {
      ok: false,
      error: "日期须为 YYYY:MM:DD HH:MM:SS（也接受 YYYY-MM-DD HH:MM:SS）",
    };
  }
  const [, yearText, monthText, dayText, hourText, minuteText, secondText = "00"] = match;
  const parts = [yearText, monthText, dayText, hourText, minuteText, secondText].map(Number);
  const [year, month, day, hour, minute, second] = parts;
  if (year < 1000 || month < 1 || month > 12 || day < 1 || day > 31 ||
      hour > 23 || minute > 59 || second > 59) {
    return { ok: false, error: "日期或时间分量超出有效范围" };
  }
  const check = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
  if (
    check.getUTCFullYear() !== year || check.getUTCMonth() !== month - 1 ||
    check.getUTCDate() !== day || check.getUTCHours() !== hour ||
    check.getUTCMinutes() !== minute || check.getUTCSeconds() !== second
  ) {
    return { ok: false, error: "该日期在日历中不存在" };
  }
  const normalized = `${yearText}:${monthText}:${dayText} ${hourText}:${minuteText}:${secondText}`;
  return { ok: true, value: normalized };
}

export function targetEntries(parsed, tag) {
  if (!parsed?.exif) return [];
  return parsed.exif.entries.filter((entry) => entry.tag === tag);
}

function intervalOverlaps(left, right) {
  return left.start < right.end && right.start < left.end;
}

function fieldRange(entry) {
  if (!entry?.writable) return null;
  return entry.writable;
}

function makeTarget(entry, plannedBytes, tagLabel) {
  const fileOffset = Number.isInteger(entry.dataFileOffset) ? entry.dataFileOffset : null;
  const length = Number.isInteger(entry.dataSize) ? entry.dataSize : 0;
  return {
    id: entry.id,
    ifdId: entry.ifdId,
    ifdPath: entry.ifdPath,
    entryOffset: entry.entryOffset,
    fileOffset,
    endOffset: fileOffset === null ? null : fileOffset + length,
    length,
    storage: entry.storage,
    typeName: entry.typeName,
    count: entry.count,
    currentValue: entry.valueText,
    valid: entry.valid,
    duplicateInIfd: Boolean(entry.duplicateInIfd),
    tagLabel,
    plannedBytes,
    rangeLabel: fileOffset === null
      ? "INVALID"
      : `${offsetLabel(fileOffset)}–${offsetLabel(fileOffset + length)}`,
  };
}

export function planEdit(parsed, options = {}) {
  const errors = [];
  const warnings = [];
  const dateResult = normalizeExifDate(options.dateText);
  const orientationText = String(options.orientation ?? "").trim();
  const orientation = /^\d+$/.test(orientationText) ? Number(orientationText) : NaN;
  if (!dateResult.ok) errors.push({ code: "invalid-date", message: dateResult.error });
  if (!Number.isInteger(orientation) || orientation < 1 || orientation > 8) {
    errors.push({ code: "invalid-orientation", message: "方向必须是 1–8 的整数" });
  }

  if (!parsed?.exif) {
    errors.push({ code: "missing-exif", message: "没有可写入的 Exif APP1 段" });
  }
  const inheritedIssues = [
    ...(parsed?.issues || []),
    ...(parsed?.exif?.issues || []),
  ];
  const seenIssues = new Set();
  for (const issue of inheritedIssues) {
    const key = `${issue.code}:${issue.offset}:${issue.message}`;
    if (seenIssues.has(key)) continue;
    seenIssues.add(key);
    if (issue.severity === "error") {
      errors.push({ code: issue.code, message: issue.message, offset: issue.offset });
    } else {
      warnings.push({ code: issue.code, message: issue.message, offset: issue.offset });
    }
  }

  const dateEntries = targetEntries(parsed, TARGET_TAGS.date);
  const orientationEntries = targetEntries(parsed, TARGET_TAGS.orientation);
  if (parsed?.exif) {
    if (dateEntries.length === 0) {
      errors.push({ code: "missing-date", message: "未找到 DateTimeOriginal (0x9003)" });
    }
    if (orientationEntries.length === 0) {
      errors.push({ code: "missing-orientation", message: "未找到 Orientation (0x0112)" });
    }
  }

  const patches = [];
  const targets = { date: [], orientation: [] };
  if (dateResult.ok) {
    const dateBytes = new TextEncoder().encode(dateResult.value);
    for (const entry of dateEntries) {
      if (!entry.valid) {
        targets.date.push(makeTarget(entry, null, "DateTimeOriginal"));
        continue;
      }
      if (entry.type !== 2) {
        errors.push({
          code: "date-type",
          message: `${entry.ifdPath}.DateTimeOriginal 类型为 ${entry.typeName}，应为 ASCII`,
        });
        continue;
      }
      if (entry.dataSize < 20) {
        errors.push({
          code: "date-capacity",
          message: `${entry.ifdPath}.DateTimeOriginal 仅 ${entry.dataSize} 字节，EXIF 日期需要至少 20 字节`,
        });
        continue;
      }
      const bytes = new Uint8Array(entry.dataSize);
      bytes.set(dateBytes, 0);
      targets.date.push(makeTarget(entry, bytes, "DateTimeOriginal"));
      patches.push({
        tag: TARGET_TAGS.date,
        tagLabel: "DateTimeOriginal",
        entryId: entry.id,
        start: entry.dataFileOffset,
        end: entry.dataFileOffset + entry.dataSize,
        bytes,
        ifdPath: entry.ifdPath,
      });
    }
  }

  if (Number.isInteger(orientation) && orientation >= 1 && orientation <= 8) {
    for (const entry of orientationEntries) {
      if (!entry.valid) {
        targets.orientation.push(makeTarget(entry, null, "Orientation"));
        continue;
      }
      if (entry.type !== 3 || entry.count !== 1) {
        errors.push({
          code: "orientation-shape",
          message: `${entry.ifdPath}.Orientation 必须为 SHORT × 1`,
        });
        continue;
      }
      const bytes = new Uint8Array([0, 0]);
      const orderView = new DataView(bytes.buffer);
      orderView.setUint16(0, orientation, parsed.exif.byteOrder === "little");
      targets.orientation.push(makeTarget(entry, bytes, "Orientation"));
      patches.push({
        tag: TARGET_TAGS.orientation,
        tagLabel: "Orientation",
        entryId: entry.id,
        start: entry.dataFileOffset,
        end: entry.dataFileOffset + 2,
        bytes,
        ifdPath: entry.ifdPath,
      });
    }
  }

  const uniquePatches = [];
  const ranges = new Map();
  for (const patch of patches) {
    const key = `${patch.start}:${patch.end}:${patch.tag}`;
    if (ranges.has(key)) continue;
    if (patch.start < 0 || patch.end > parsed.fileSize) {
      errors.push({
        code: "patch-oob",
        message: `${patch.tagLabel} 写入区间 ${offsetLabel(patch.start)}–${offsetLabel(patch.end)} 超出文件边界`,
      });
    }
    const conflict = patches.find((other) =>
      other !== patch &&
      intervalOverlaps({ start: patch.start, end: patch.end }, { start: other.start, end: other.end }) &&
      !(other.start === patch.start && other.end === patch.end && other.tag === patch.tag));
    if (conflict) {
      errors.push({
        code: "patch-overlap",
        message: `${patch.ifdPath} 的 ${patch.tagLabel} 写入区间与 ${conflict.tagLabel} 重叠`,
      });
    }
    ranges.set(key, patch);
    uniquePatches.push(patch);
  }

  if (parsed?.exif?.ifds) {
    for (const patch of uniquePatches) {
      const target = [...targets.date, ...targets.orientation]
        .find((item) => item.id === patch.entryId);
      if (target?.storage !== "inline") {
        for (const ifd of parsed.exif.ifds) {
          if (ifd.tableStart === null || ifd.tableEnd === null) continue;
          if (intervalOverlaps(
            { start: patch.start, end: patch.end },
            { start: ifd.tableStart, end: ifd.tableEnd },
          )) {
            errors.push({
              code: "target-hits-ifd",
              message: `${patch.ifdPath} 的 ${patch.tagLabel} 数据区间与 IFD 表重叠`,
            });
          }
        }
      }
      for (const entry of parsed.exif.entries) {
        if (entry.id === patch.entryId || !entry.writable || !entry.valid) continue;
        if (entry.tag === patch.tag) continue;
        if (intervalOverlaps(
          { start: patch.start, end: patch.end },
          { start: entry.writable.start, end: entry.writable.end },
        )) {
          errors.push({
            code: "target-hits-field",
            message: `${patch.ifdPath} 的 ${patch.tagLabel} 数据区间与 ${entry.ifdPath}.${entry.tagLabel} 重叠`,
          });
        }
      }
    }
  }

  if (targets.date.some((target) => target.duplicateInIfd)) {
    warnings.push({
      code: "duplicate-date",
      message: `检测到同一 IFD 内重复 DateTimeOriginal × ${dateEntries.length}，将同步写入全部副本`,
    });
  }
  if (targets.orientation.some((target) => target.duplicateInIfd)) {
    warnings.push({
      code: "duplicate-orientation",
      message: `检测到同一 IFD 内重复 Orientation × ${orientationEntries.length}，将同步写入全部副本`,
    });
  }

  const dedupedErrors = dedupeDiagnostics(errors);
  const success = dedupedErrors.length === 0 && dateResult.ok &&
    Number.isInteger(orientation) && orientation >= 1 && orientation <= 8;
  return {
    ok: success,
    canWrite: success,
    dateText: dateResult.ok ? dateResult.value : null,
    orientation: Number.isInteger(orientation) ? orientation : null,
    targets,
    patches: uniquePatches,
    plannedRanges: uniquePatches.map((patch) => ({
      offset: patch.start,
      end: patch.end,
      length: patch.end - patch.start,
      label: `${patch.ifdPath}.${patch.tagLabel}`,
    })),
    errors: dedupedErrors,
    warnings: dedupeDiagnostics(warnings),
    scanHash: parsed?.scan?.hash || null,
    scanRange: parsed?.scan ? { start: parsed.scan.start, end: parsed.scan.end } : null,
  };
}

function dedupeDiagnostics(items) {
  const seen = new Set();
  return items.filter((item) => {
    const key = `${item.code}:${item.offset ?? ""}:${item.message}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function applyEdit(parsed, options = {}) {
  const plan = options.plan || planEdit(parsed, options);
  if (!plan.ok) {
    return {
      ok: false,
      plan,
      output: null,
      changedRanges: [],
      changedBytes: [],
      errors: plan.errors,
      verification: null,
    };
  }

  const before = parsed.bytes;
  const output = before.slice();
  for (const patch of plan.patches) {
    output.set(patch.bytes, patch.start);
  }

  const afterParsed = parseJpeg(output);
  const changedBytes = [];
  for (let offset = 0; offset < before.length; offset += 1) {
    if (before[offset] !== output[offset]) {
      changedBytes.push({
        offset,
        before: before[offset],
        after: output[offset],
      });
    }
  }

  const runs = [];
  for (const change of changedBytes) {
    const previous = runs[runs.length - 1];
    if (previous && previous.end === change.offset) {
      previous.end += 1;
      previous.afterBytes.push(change.after);
      previous.beforeBytes.push(change.before);
      previous.byteCount += 1;
    } else {
      runs.push({
        start: change.offset,
        end: change.offset + 1,
        byteCount: 1,
        beforeBytes: [change.before],
        afterBytes: [change.after],
      });
    }
  }
  for (const run of runs) {
    run.length = run.end - run.start;
    run.beforeHex = bytesToHex(run.beforeBytes);
    run.afterHex = bytesToHex(run.afterBytes);
    run.labels = [...new Set(plan.patches
      .filter((patch) => run.start < patch.end && patch.start < run.end)
      .map((patch) => `${patch.ifdPath}.${patch.tagLabel}`))];
  }

  const onlyTargetBytes = changedBytes.every((change) =>
    plan.patches.some((patch) => change.offset >= patch.start && change.offset < patch.end));
  const scanBeforeHash = parsed.scan?.hash || null;
  const scanAfterHash = afterParsed.scan?.hash || null;
  const scanUnchanged = Boolean(scanBeforeHash && scanAfterHash && scanBeforeHash === scanAfterHash);
  const lengthUnchanged = output.length === before.length;
  const app1BoundsUnchanged = Boolean(
    afterParsed.exif && parsed.exif &&
    afterParsed.exif.tiffStart === parsed.exif.tiffStart &&
    afterParsed.exif.tiffEnd === parsed.exif.tiffEnd &&
    afterParsed.exif.byteOrder === parsed.exif.byteOrder,
  );
  const afterDateEntries = targetEntries(afterParsed, TARGET_TAGS.date);
  const afterOrientationEntries = targetEntries(afterParsed, TARGET_TAGS.orientation);
  const valuesWritten =
    afterDateEntries.length === plan.targets.date.length &&
    afterOrientationEntries.length === plan.targets.orientation.length &&
    afterDateEntries.every((entry) => entry.valid && entry.ascii === plan.dateText) &&
    afterOrientationEntries.every((entry) =>
      entry.valid && entry.type === 3 && entry.count === 1 && entry.numbers?.[0] === plan.orientation);
  const reparsedClean = afterParsed.valid;
  const verificationPassed =
    scanUnchanged && lengthUnchanged && app1BoundsUnchanged &&
    onlyTargetBytes && valuesWritten && reparsedClean;

  const checks = [
    {
      id: "scan",
      label: "扫描数据 SHA-256",
      passed: scanUnchanged,
      detail: scanUnchanged ? `${scanBeforeHash.slice(0, 16)}… 未变化` : "扫描数据发生变化",
    },
    {
      id: "length",
      label: "文件长度不扩张",
      passed: lengthUnchanged,
      detail: `${before.length.toLocaleString()} B → ${output.length.toLocaleString()} B`,
    },
    {
      id: "bounds",
      label: "APP1 / TIFF 边界",
      passed: app1BoundsUnchanged,
      detail: app1BoundsUnchanged ? "偏移与字节序保持原位" : "Exif 容器边界发生移动",
    },
    {
      id: "scope",
      label: "修改范围",
      passed: onlyTargetBytes,
      detail: onlyTargetBytes ? "全部变更位于目标标签字段内" : "检测到目标字段外变更",
    },
    {
      id: "values",
      label: "回读目标值",
      passed: valuesWritten,
      detail: valuesWritten ? "DateTimeOriginal / Orientation 回读一致" : "回读值不一致",
    },
    {
      id: "reparse",
      label: "输出重新解析",
      passed: reparsedClean,
      detail: reparsedClean ? "结构、偏移与扫描段均有效" : "输出结构校验失败",
    },
  ];

  const verification = {
    passed: verificationPassed,
    checks,
    scan: {
      start: parsed.scan?.start ?? null,
      end: parsed.scan?.end ?? null,
      length: parsed.scan?.length ?? null,
      beforeHash: scanBeforeHash,
      afterHash: scanAfterHash,
      equal: scanUnchanged,
    },
    changedByteCount: changedBytes.length,
    changedRunCount: runs.length,
    afterDateEntries,
    afterOrientationEntries,
  };

  if (!verificationPassed) {
    return {
      ok: false,
      plan,
      output: null,
      changedRanges: runs,
      changedBytes,
      errors: [{ code: "verification-failed", message: "定点补丁未通过写出前验证，已拒绝输出" }],
      verification,
    };
  }

  return {
    ok: true,
    plan,
    output,
    changedRanges: runs,
    changedBytes,
    errors: [],
    verification,
    after: afterParsed,
  };
}

export function describeByteRange(range, fileSize) {
  const width = Math.max(6, String(fileSize.toString(16).length));
  return `${offsetLabel(range.start).padStart(width + 2, "0")} +${range.length}`;
}
