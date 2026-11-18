'use strict';

const { GifWriter } = require('omggif');

const PALETTE = [
  0x101820,
  0xe63946,
  0x2ec4b6,
  0xffd166,
  0x4361ee,
  0xf8f9fa,
  0x5fbf77,
  0x000000,
];

const TRANSPARENT = 7;

function indexedFrame(width, height, chooseIndex) {
  const pixels = new Uint8Array(width * height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      pixels[y * width + x] = chooseIndex(x, y);
    }
  }
  return pixels;
}

function encodeGif(width, height, frames) {
  const buffer = Buffer.alloc(2 * 1024 * 1024);
  const writer = new GifWriter(buffer, width, height, {
    palette: PALETTE,
    loop: 0,
  });
  for (const frame of frames) {
    writer.addFrame(
      frame.left,
      frame.top,
      frame.width,
      frame.height,
      frame.pixels,
      {
        delay: frame.delay,
        disposal: frame.disposal,
        transparent: frame.transparent ? TRANSPARENT : undefined,
      },
    );
  }
  const length = writer.end();
  return Buffer.from(buffer.subarray(0, length));
}

function correctFrameDefinitions() {
  return [
    {
      name: 'full opaque base',
      left: 0,
      top: 0,
      width: 16,
      height: 16,
      delay: 10,
      disposal: 1,
      transparent: false,
      pixels: indexedFrame(16, 16, (x, y) => {
        if (x < 2 || y < 2) return 0;
        if (y < 6) return 3;
        return x < 8 ? 1 : 2;
      }),
    },
    {
      name: 'small frame with transparent window',
      left: 2,
      top: 3,
      width: 6,
      height: 6,
      delay: 10,
      disposal: 1,
      transparent: true,
      pixels: indexedFrame(6, 6, (x, y) => {
        if (x >= 2 && x <= 3 && y >= 1 && y <= 2) return TRANSPARENT;
        return 1;
      }),
    },
    {
      name: 'overlapping transparent frame',
      left: 5,
      top: 5,
      width: 6,
      height: 6,
      delay: 10,
      disposal: 3,
      transparent: true,
      pixels: indexedFrame(6, 6, (x, y) => {
        if (x === y || x === 0 || y === 5) return TRANSPARENT;
        return 2;
      }),
    },
    {
      name: 'restore-background frame',
      left: 9,
      top: 2,
      width: 4,
      height: 4,
      delay: 10,
      disposal: 2,
      transparent: false,
      pixels: indexedFrame(4, 4, () => 4),
    },
    {
      name: 'restore-previous overlap probe',
      left: 7,
      top: 3,
      width: 6,
      height: 5,
      delay: 10,
      disposal: 3,
      transparent: true,
      pixels: indexedFrame(6, 5, (x, y) => {
        if ((x === 2 || x === 3) && (y === 2 || y === 3)) return TRANSPARENT;
        return 6;
      }),
    },
    {
      name: 'persistent transparent accent',
      left: 3,
      top: 11,
      width: 5,
      height: 4,
      delay: 10,
      disposal: 1,
      transparent: true,
      pixels: indexedFrame(5, 4, (x, y) => {
        if (x >= 1 && x <= 3 && y >= 1 && y <= 2) return TRANSPARENT;
        return 1;
      }),
    },
    {
      name: 'full opaque loop barrier',
      left: 0,
      top: 0,
      width: 16,
      height: 16,
      delay: 10,
      disposal: 2,
      transparent: false,
      pixels: indexedFrame(16, 16, (x, y) => {
        if (x === 0 || y === 0 || x === 15 || y === 15) return 0;
        return 5;
      }),
    },
  ];
}

function leakyFrameDefinitions() {
  return [
    {
      name: 'local first frame leaves corners untouched',
      left: 2,
      top: 2,
      width: 12,
      height: 12,
      delay: 10,
      disposal: 1,
      transparent: false,
      pixels: indexedFrame(12, 12, (x, y) => {
        if (x % 4 === 0 || y % 4 === 0) return 3;
        return 1;
      }),
    },
    {
      name: 'small transparent overlap',
      left: 2,
      top: 3,
      width: 6,
      height: 6,
      delay: 10,
      disposal: 1,
      transparent: true,
      pixels: indexedFrame(6, 6, (x, y) => (
        x >= 2 && x <= 3 && y >= 1 && y <= 2 ? TRANSPARENT : 2
      )),
    },
    {
      name: 'restore previous',
      left: 5,
      top: 5,
      width: 6,
      height: 6,
      delay: 10,
      disposal: 3,
      transparent: true,
      pixels: indexedFrame(6, 6, (x, y) => (
        x === y || x === 0 || y === 5 ? TRANSPARENT : 4
      )),
    },
    {
      name: 'persistent small last frame',
      left: 0,
      top: 0,
      width: 8,
      height: 8,
      delay: 10,
      disposal: 1,
      transparent: false,
      pixels: indexedFrame(8, 8, (x, y) => (
        (x + y) % 3 === 0 ? 6 : 5
      )),
    },
  ];
}

function buildCorrectSample() {
  return encodeGif(16, 16, correctFrameDefinitions());
}

function buildLeakySample() {
  return encodeGif(16, 16, leakyFrameDefinitions());
}

module.exports = {
  PALETTE,
  TRANSPARENT,
  buildCorrectSample,
  buildLeakySample,
  correctFrameDefinitions,
  leakyFrameDefinitions,
};
