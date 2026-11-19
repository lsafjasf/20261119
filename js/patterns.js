(function (global) {
  "use strict";

  const SIZE = 128;

  function clampByte(value) {
    return Math.max(0, Math.min(255, Math.round(value)));
  }

  function createRgb(pattern) {
    const rgb = new Uint8ClampedArray(SIZE * SIZE * 3);

    for (let y = 0; y < SIZE; y += 1) {
      for (let x = 0; x < SIZE; x += 1) {
        let red;
        let green;
        let blue;

        if (pattern === "diagonal") {
          const wave =
            128 + 78 * Math.sin(((x + y) * Math.PI * 2) / 18) +
            8 * Math.sin((x - y) * 0.08);
          red = wave;
          green = wave;
          blue = wave;
        } else if (pattern === "checker") {
          const value = (x + y) % 2 === 0 ? 28 : 230;
          red = value;
          green = value;
          blue = value;
        } else if (pattern === "color") {
          const split = x + y < SIZE;
          const shimmer = 10 * Math.sin(y * 0.12);
          if (split) {
            red = 224 + shimmer;
            green = 92 + shimmer * 0.5;
            blue = 54;
          } else {
            red = 25;
            green = 126 + shimmer * 0.4;
            blue = 146 + shimmer;
          }
        } else {
          red = 126;
          green = 138;
          blue = 152;
        }

        const index = (y * SIZE + x) * 3;
        rgb[index] = clampByte(red);
        rgb[index + 1] = clampByte(green);
        rgb[index + 2] = clampByte(blue);
      }
    }
    return rgb;
  }

  function rgbToYCbCr(rgb) {
    const length = SIZE * SIZE;
    const planes = {
      Y: new Uint8ClampedArray(length),
      Cb: new Uint8ClampedArray(length),
      Cr: new Uint8ClampedArray(length),
    };

    for (let i = 0; i < length; i += 1) {
      const red = rgb[i * 3];
      const green = rgb[i * 3 + 1];
      const blue = rgb[i * 3 + 2];

      planes.Y[i] = clampByte(0.299 * red + 0.587 * green + 0.114 * blue);
      planes.Cb[i] = clampByte(128 - 0.168736 * red - 0.331264 * green + 0.5 * blue);
      planes.Cr[i] = clampByte(128 + 0.5 * red - 0.418688 * green - 0.081312 * blue);
    }
    return planes;
  }

  function createSample(pattern) {
    const key = ["flat", "diagonal", "checker", "color"].includes(pattern)
      ? pattern
      : "flat";
    const rgb = createRgb(key);
    return {
      key,
      width: SIZE,
      height: SIZE,
      rgb,
      planes: rgbToYCbCr(rgb),
    };
  }

  const api = { SIZE, createSample, rgbToYCbCr };
  global.JpegPatterns = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
