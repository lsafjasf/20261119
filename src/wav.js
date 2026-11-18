const WAVE_FORMAT_PCM = 1;
const WAVE_FORMAT_IEEE_FLOAT = 3;
const WAVE_FORMAT_EXTENSIBLE = 0xfffe;

function ascii(bytes, start, length) {
  let value = "";
  for (let index = 0; index < length; index += 1) {
    value += String.fromCharCode(bytes[start + index]);
  }
  return value;
}

function uint16(bytes, offset) {
  return bytes[offset] | (bytes[offset + 1] << 8);
}

function uint32(bytes, offset) {
  return (
    bytes[offset] |
    (bytes[offset + 1] << 8) |
    (bytes[offset + 2] << 16) |
    (bytes[offset + 3] << 24)
  ) >>> 0;
}

function fail(message) {
  throw new Error(`Unsupported WAV: ${message}`);
}

export function bytesSource(bytes, name = "memory.wav") {
  const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  return {
    name,
    size: data.byteLength,
    async read(offset, length) {
      return data.subarray(offset, Math.min(data.byteLength, offset + length));
    },
  };
}

export function blobSource(blob, name = blob.name || "audio.wav") {
  return {
    name,
    size: blob.size,
    async read(offset, length) {
      const buffer = await blob.slice(offset, offset + length).arrayBuffer();
      return new Uint8Array(buffer);
    },
  };
}

export async function readExact(source, offset, length) {
  if (offset < 0 || length < 0 || offset + length > source.size) {
    throw new RangeError("Read is outside the source.");
  }
  const output = new Uint8Array(length);
  let written = 0;
  while (written < length) {
    const part = await source.read(offset + written, length - written);
    if (!part || part.byteLength === 0) {
      throw new Error("Unexpected end of source.");
    }
    const count = Math.min(part.byteLength, length - written);
    output.set(part.subarray(0, count), written);
    written += count;
  }
  return output;
}

function parseFormatChunk(bytes) {
  if (bytes.byteLength < 16) {
    fail("the fmt chunk is truncated.");
  }

  let formatTag = uint16(bytes, 0);
  const channels = uint16(bytes, 2);
  const sampleRate = uint32(bytes, 4);
  const byteRate = uint32(bytes, 8);
  const blockAlign = uint16(bytes, 12);
  const bitsPerSample = uint16(bytes, 14);
  let validBitsPerSample = bitsPerSample;

  if (formatTag === WAVE_FORMAT_EXTENSIBLE) {
    if (bytes.byteLength < 40 || uint16(bytes, 16) < 22) {
      fail("the extensible fmt chunk is truncated.");
    }
    validBitsPerSample = uint16(bytes, 18);
    formatTag = uint16(bytes, 24);
  }

  const isInteger = formatTag === WAVE_FORMAT_PCM;
  const isFloat = formatTag === WAVE_FORMAT_IEEE_FLOAT;
  if (!isInteger && !isFloat) {
    fail(`format tag ${formatTag} is not integer PCM or IEEE float.`);
  }
  if (channels < 1) {
    fail("channel count is zero.");
  }
  if (sampleRate < 1) {
    fail("sample rate is zero.");
  }

  const bytesPerSample = Math.ceil(bitsPerSample / 8);
  if (isInteger && ![8, 16, 24, 32].includes(bitsPerSample)) {
    fail(`${bitsPerSample}-bit integer PCM is not supported.`);
  }
  if (isFloat && ![32, 64].includes(bitsPerSample)) {
    fail(`${bitsPerSample}-bit float PCM is not supported.`);
  }
  if (blockAlign !== channels * bytesPerSample) {
    fail("block alignment does not match channels and bit depth.");
  }
  if (byteRate !== sampleRate * blockAlign) {
    fail("byte rate does not match sample rate and block alignment.");
  }
  if (validBitsPerSample < 1 || validBitsPerSample > bitsPerSample) {
    fail("valid bit depth is invalid.");
  }

  return {
    formatTag,
    encoding: isFloat ? "float" : "integer",
    channels,
    sampleRate,
    byteRate,
    blockAlign,
    bitsPerSample,
    validBitsPerSample,
    bytesPerSample,
  };
}

export async function parseWav(source) {
  if (!source || !Number.isFinite(source.size) || source.size < 12) {
    fail("file is too small.");
  }

  const header = await readExact(source, 0, 12);
  if (ascii(header, 0, 4) !== "RIFF" || ascii(header, 8, 4) !== "WAVE") {
    fail("missing RIFF/WAVE signature.");
  }

  let format = null;
  let dataOffset = -1;
  let dataLength = 0;
  let offset = 12;

  while (offset + 8 <= source.size) {
    const chunkHeader = await readExact(source, offset, 8);
    const id = ascii(chunkHeader, 0, 4);
    const chunkSize = uint32(chunkHeader, 4);
    const payloadOffset = offset + 8;

    if (id === "fmt ") {
      const fmtLength = Math.min(chunkSize, 64);
      format = parseFormatChunk(await readExact(source, payloadOffset, fmtLength));
    } else if (id === "data") {
      if (!format) {
        fail("the data chunk appears before the fmt chunk.");
      }
      const available = source.size - payloadOffset;
      dataOffset = payloadOffset;
      dataLength = Math.min(chunkSize === 0xffffffff ? available : chunkSize, available);
      break;
    }

    const paddedSize = chunkSize + (chunkSize & 1);
    if (payloadOffset + paddedSize <= offset) {
      fail("invalid chunk size.");
    }
    offset = payloadOffset + paddedSize;
  }

  if (!format) {
    fail("the fmt chunk is missing.");
  }
  if (dataOffset < 0) {
    fail("the data chunk is missing.");
  }

  const frames = Math.floor(dataLength / format.blockAlign);
  const completeDataLength = frames * format.blockAlign;

  return {
    ...format,
    dataOffset,
    dataLength: completeDataLength,
    frames,
    duration: frames / format.sampleRate,
  };
}

export function createWavHeader(format, dataLength = format.dataLength) {
  if (dataLength > 0xffffffff - 36) {
    throw new RangeError("WAV output exceeds the 4 GiB RIFF limit.");
  }
  const header = new Uint8Array(44);
  const view = new DataView(header.buffer);
  const writeAscii = (offset, text) => {
    for (let index = 0; index < text.length; index += 1) {
      header[offset + index] = text.charCodeAt(index);
    }
  };

  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + dataLength, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, format.encoding === "float" ? WAVE_FORMAT_IEEE_FLOAT : WAVE_FORMAT_PCM, true);
  view.setUint16(22, format.channels, true);
  view.setUint32(24, format.sampleRate, true);
  view.setUint32(28, format.byteRate, true);
  view.setUint16(32, format.blockAlign, true);
  view.setUint16(34, format.bitsPerSample, true);
  writeAscii(36, "data");
  view.setUint32(40, dataLength, true);
  return header;
}

