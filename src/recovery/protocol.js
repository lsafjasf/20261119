// Lightweight, dependency-free "media segment" wire format used by the
// simulated player and the local fault injection service.
//
// Wire layout (all integers big-endian):
//   magic    : 4 bytes  = "VSEG"
//   version  : 1 byte
//   seq      : uint32
//   start    : float64 (seconds, presentation timeline)
//   duration : float64 (seconds)
//   bitrate  : uint32  (kbps)
//   crc32    : uint32  (over payload only)
//   payload  : remaining bytes, utf-8 JSON with { frames: [{ pts, key }] }

export const SEGMENT_MAGIC = Buffer.from("VSEG");
export const PROTOCOL_VERSION = 1;

export const HEADER_SIZE = 4 + 1 + 4 + 8 + 8 + 4 + 4;

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  return table;
})();

export function crc32(buffer) {
  let crc = 0xffffffff;
  for (let i = 0; i < buffer.length; i += 1) {
    crc = CRC_TABLE[(crc ^ buffer[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

export function encodeSegment(meta, payloadObject) {
  const payload = Buffer.from(JSON.stringify(payloadObject), "utf8");
  const header = Buffer.alloc(HEADER_SIZE);
  let offset = 0;
  SEGMENT_MAGIC.copy(header, offset);
  offset += 4;
  header.writeUInt8(PROTOCOL_VERSION, offset);
  offset += 1;
  header.writeUInt32BE(meta.seq, offset);
  offset += 4;
  header.writeDoubleBE(meta.start, offset);
  offset += 8;
  header.writeDoubleBE(meta.duration, offset);
  offset += 8;
  header.writeUInt32BE(meta.bitrate, offset);
  offset += 4;
  header.writeUInt32BE(crc32(payload), offset);
  return Buffer.concat([header, payload]);
}

export function decodeSegment(buffer) {
  if (buffer.length < HEADER_SIZE || !buffer.subarray(0, 4).equals(SEGMENT_MAGIC)) {
    throw new Error("malformed segment: bad magic");
  }
  let offset = 4;
  const version = buffer.readUInt8(offset);
  offset += 1;
  if (version !== PROTOCOL_VERSION) {
    throw new Error(`unsupported segment version: ${version}`);
  }
  const seq = buffer.readUInt32BE(offset);
  offset += 4;
  const start = buffer.readDoubleBE(offset);
  offset += 8;
  const duration = buffer.readDoubleBE(offset);
  offset += 8;
  const bitrate = buffer.readUInt32BE(offset);
  offset += 4;
  const expectedCrc = buffer.readUInt32BE(offset);
  const payload = buffer.subarray(HEADER_SIZE);
  const actualCrc = crc32(payload);
  if (actualCrc !== expectedCrc) {
    throw new Error(
      `corrupt segment seq=${seq}: crc mismatch expected=${expectedCrc} actual=${actualCrc}`,
    );
  }
  let parsed;
  try {
    parsed = JSON.parse(payload.toString("utf8"));
  } catch (cause) {
    throw new Error(`corrupt segment seq=${seq}: payload is not valid JSON`, { cause });
  }
  if (!parsed || !Array.isArray(parsed.frames)) {
    throw new Error(`corrupt segment seq=${seq}: missing frames`);
  }
  return { seq, start, duration, bitrate, frames: parsed.frames };
}
