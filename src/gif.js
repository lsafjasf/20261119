'use strict';

function fail(message) {
  throw new Error(`GIF parse error: ${message}`);
}

function readColorTable(buffer, offset, entries) {
  const byteLength = entries * 3;
  if (offset + byteLength > buffer.length) {
    fail('truncated color table');
  }
  const palette = new Uint8Array(entries * 4);
  for (let i = 0; i < entries; i += 1) {
    palette[i * 4] = buffer[offset + i * 3];
    palette[i * 4 + 1] = buffer[offset + i * 3 + 1];
    palette[i * 4 + 2] = buffer[offset + i * 3 + 2];
    palette[i * 4 + 3] = 255;
  }
  return { palette, offset: offset + byteLength, entries };
}

function readSubBlocks(buffer, start) {
  const chunks = [];
  let offset = start;
  let total = 0;
  while (true) {
    if (offset >= buffer.length) {
      fail('truncated sub-block sequence');
    }
    const size = buffer[offset];
    offset += 1;
    if (size === 0) {
      break;
    }
    if (offset + size > buffer.length) {
      fail('truncated sub-block payload');
    }
    chunks.push(buffer.subarray(offset, offset + size));
    total += size;
    offset += size;
  }
  return { data: Buffer.concat(chunks, total), offset };
}

function decodeLzw(minCodeSize, compressed, expectedPixels) {
  if (minCodeSize < 2 || minCodeSize > 8) {
    fail(`invalid LZW minimum code size ${minCodeSize}`);
  }

  const clearCode = 1 << minCodeSize;
  const endCode = clearCode + 1;
  let codeSize = minCodeSize + 1;
  let nextCode = endCode + 1;
  let dictionary = [];
  let previous = null;
  const output = [];

  function resetDictionary() {
    dictionary = new Array(4096);
    for (let i = 0; i < clearCode; i += 1) {
      dictionary[i] = [i];
    }
    codeSize = minCodeSize + 1;
    nextCode = endCode + 1;
    previous = null;
  }

  let bitBuffer = 0;
  let bitCount = 0;
  let inputOffset = 0;

  function readCode() {
    while (bitCount < codeSize) {
      if (inputOffset >= compressed.length) {
        return null;
      }
      bitBuffer |= compressed[inputOffset] << bitCount;
      inputOffset += 1;
      bitCount += 8;
    }
    const code = bitBuffer & ((1 << codeSize) - 1);
    bitBuffer >>>= codeSize;
    bitCount -= codeSize;
    return code;
  }

  resetDictionary();

  while (true) {
    const code = readCode();
    if (code === null || code === endCode) {
      break;
    }
    if (code === clearCode) {
      resetDictionary();
      continue;
    }

    let entry;
    if (code < nextCode && dictionary[code]) {
      entry = dictionary[code];
    } else if (code === nextCode && previous) {
      entry = previous.concat(previous[0]);
    } else {
      fail(`invalid LZW code ${code} (next=${nextCode})`);
    }

    for (const value of entry) {
      output.push(value);
    }
    if (output.length > expectedPixels) {
      fail('LZW stream exceeds frame dimensions');
    }

    if (previous && nextCode < 4096) {
      dictionary[nextCode] = previous.concat(entry[0]);
      nextCode += 1;
      if (nextCode === (1 << codeSize) && codeSize < 12) {
        codeSize += 1;
      }
    }
    previous = entry;
  }

  if (output.length !== expectedPixels) {
    fail(`decoded ${output.length} pixels, expected ${expectedPixels}`);
  }
  return Uint8Array.from(output);
}

function deinterlace(indices, width, height) {
  const result = new Uint8Array(indices.length);
  let sourceRow = 0;
  const passes = [
    [0, 8],
    [4, 8],
    [2, 4],
    [1, 2],
  ];
  for (const [start, step] of passes) {
    for (let destinationRow = start; destinationRow < height; destinationRow += step) {
      const sourceOffset = sourceRow * width;
      result.set(
        indices.subarray(sourceOffset, sourceOffset + width),
        destinationRow * width,
      );
      sourceRow += 1;
    }
  }
  return result;
}

function normalizeTransparentIndex(transparentIndex) {
  return transparentIndex === null || transparentIndex === undefined
    ? null
    : transparentIndex;
}

function parseGif(input) {
  const buffer = Buffer.isBuffer(input) ? input : Buffer.from(input);
  if (buffer.length < 13) {
    fail('file is too short');
  }
  const signature = buffer.toString('ascii', 0, 6);
  if (signature !== 'GIF87a' && signature !== 'GIF89a') {
    fail(`unsupported signature ${JSON.stringify(signature)}`);
  }

  let offset = 6;
  const width = buffer.readUInt16LE(offset);
  const height = buffer.readUInt16LE(offset + 2);
  const packed = buffer[offset + 4];
  const backgroundIndex = buffer[offset + 5];
  offset += 7;

  if (width === 0 || height === 0) {
    fail('logical screen has zero dimensions');
  }

  const globalTableFlag = (packed & 0x80) !== 0;
  const globalEntries = 1 << ((packed & 0x07) + 1);
  let globalColorTable = null;
  if (globalTableFlag) {
    const table = readColorTable(buffer, offset, globalEntries);
    globalColorTable = table.palette;
    offset = table.offset;
  }

  let loopCount = null;
  let pendingControl = {
    disposal: 0,
    delay: 0,
    transparentIndex: null,
  };
  const frames = [];
  let trailerSeen = false;

  while (offset < buffer.length) {
    const introducer = buffer[offset];
    offset += 1;

    if (introducer === 0x3b) {
      trailerSeen = true;
      break;
    }

    if (introducer === 0x21) {
      if (offset >= buffer.length) {
        fail('truncated extension');
      }
      const label = buffer[offset];
      offset += 1;

      if (label === 0xf9) {
        if (offset >= buffer.length || buffer[offset] !== 4) {
          fail('malformed graphics control extension');
        }
        const controlPacked = buffer[offset + 1];
        const delay = buffer.readUInt16LE(offset + 2);
        const transparentIndex = buffer[offset + 4];
        pendingControl = {
          disposal: (controlPacked >> 2) & 0x07,
          delay,
          transparentIndex: (controlPacked & 0x01) !== 0
            ? normalizeTransparentIndex(transparentIndex)
            : null,
        };
        offset += 5;
        if (offset >= buffer.length || buffer[offset] !== 0) {
          fail('graphics control extension missing terminator');
        }
        offset += 1;
        continue;
      }

      if (label === 0xff) {
        if (offset >= buffer.length) {
          fail('truncated application extension');
        }
        const identifierSize = buffer[offset];
        offset += 1;
        if (offset + identifierSize > buffer.length) {
          fail('truncated application identifier');
        }
        const identifier = buffer.toString('ascii', offset, offset + identifierSize);
        offset += identifierSize;
        const subBlocks = readSubBlocks(buffer, offset);
        offset = subBlocks.offset;
        if (
          (identifier === 'NETSCAPE2.0' || identifier === 'ANIMEXTS1.0')
          && subBlocks.data.length >= 3
          && subBlocks.data[0] === 1
        ) {
          loopCount = subBlocks.data.readUInt16LE(1);
        }
        continue;
      }

      const skipped = readSubBlocks(buffer, offset);
      offset = skipped.offset;
      continue;
    }

    if (introducer !== 0x2c) {
      fail(`unknown block introducer 0x${introducer.toString(16)} at ${offset - 1}`);
    }

    if (offset + 9 > buffer.length) {
      fail('truncated image descriptor');
    }
    const left = buffer.readUInt16LE(offset);
    const top = buffer.readUInt16LE(offset + 2);
    const frameWidth = buffer.readUInt16LE(offset + 4);
    const frameHeight = buffer.readUInt16LE(offset + 6);
    const imagePacked = buffer[offset + 8];
    offset += 9;
    if (frameWidth === 0 || frameHeight === 0) {
      fail('frame has zero dimensions');
    }

    let palette = globalColorTable;
    if ((imagePacked & 0x80) !== 0) {
      const localEntries = 1 << ((imagePacked & 0x07) + 1);
      const table = readColorTable(buffer, offset, localEntries);
      palette = table.palette;
      offset = table.offset;
    }
    if (!palette) {
      fail('frame has neither a global nor local color table');
    }

    if (offset >= buffer.length) {
      fail('truncated image data');
    }
    const minCodeSize = buffer[offset];
    offset += 1;
    const imageData = readSubBlocks(buffer, offset);
    offset = imageData.offset;
    let indices = decodeLzw(
      minCodeSize,
      imageData.data,
      frameWidth * frameHeight,
    );
    const interlaced = (imagePacked & 0x40) !== 0;
    if (interlaced) {
      indices = deinterlace(indices, frameWidth, frameHeight);
    }

    frames.push({
      index: frames.length,
      left,
      top,
      width: frameWidth,
      height: frameHeight,
      interlaced,
      palette,
      disposal: pendingControl.disposal,
      delay: pendingControl.delay,
      transparentIndex: pendingControl.transparentIndex,
      indices,
    });
    pendingControl = {
      disposal: 0,
      delay: 0,
      transparentIndex: null,
    };
  }

  if (!trailerSeen) {
    fail('missing trailer');
  }

  return {
    version: signature,
    width,
    height,
    backgroundIndex,
    globalColorTable,
    loopCount,
    frames,
  };
}

function paletteColor(palette, index, fallback = [0, 0, 0, 0]) {
  if (!palette || index * 4 + 3 >= palette.length) {
    return fallback;
  }
  return [
    palette[index * 4],
    palette[index * 4 + 1],
    palette[index * 4 + 2],
    palette[index * 4 + 3],
  ];
}

function backgroundRgba(gif) {
  return paletteColor(gif.globalColorTable, gif.backgroundIndex);
}

function frameRgba(frame) {
  const rgba = new Uint8Array(frame.width * frame.height * 4);
  for (let i = 0; i < frame.indices.length; i += 1) {
    const index = frame.indices[i];
    if (frame.transparentIndex !== null && index === frame.transparentIndex) {
      continue;
    }
    const color = paletteColor(frame.palette, index);
    rgba.set(color, i * 4);
  }
  return rgba;
}

module.exports = {
  backgroundRgba,
  decodeLzw,
  frameRgba,
  paletteColor,
  parseGif,
};
