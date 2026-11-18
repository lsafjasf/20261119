'use strict';

const { decompressFrames, parseGIF } = require('gifuct-js');
const { compareCanvases } = require('./compositor');
const { frameRgba } = require('./gif');

function asArrayBuffer(buffer) {
  return buffer.buffer.slice(
    buffer.byteOffset,
    buffer.byteOffset + buffer.byteLength,
  );
}

function normalizedIndex(value) {
  return value === undefined || value === null ? null : value;
}

function referenceBackground(parsed) {
  const index = parsed.lsd.backgroundColorIndex;
  if (parsed.gct && parsed.gct[index]) {
    return [...parsed.gct[index], 255];
  }
  return [0, 0, 0, 0];
}

function createReferenceCanvas(parsed) {
  const width = parsed.lsd.width;
  const height = parsed.lsd.height;
  const pixels = new Uint8Array(width * height * 4);
  const background = referenceBackground(parsed);
  for (let offset = 0; offset < pixels.length; offset += 4) {
    pixels.set(background, offset);
  }
  return { width, height, pixels };
}

function clippedBounds(width, height, dims) {
  return {
    x0: Math.max(0, dims.left),
    y0: Math.max(0, dims.top),
    x1: Math.min(width, dims.left + dims.width),
    y1: Math.min(height, dims.top + dims.height),
  };
}

function paintReferenceFrame(canvas, referenceFrame) {
  const bounds = clippedBounds(canvas.width, canvas.height, referenceFrame.dims);
  for (let y = bounds.y0; y < bounds.y1; y += 1) {
    for (let x = bounds.x0; x < bounds.x1; x += 1) {
      const localX = x - referenceFrame.dims.left;
      const localY = y - referenceFrame.dims.top;
      const source = (localY * referenceFrame.dims.width + localX) * 4;
      if (referenceFrame.patch[source + 3] === 0) {
        continue;
      }
      const destination = (y * canvas.width + x) * 4;
      canvas.pixels[destination] = referenceFrame.patch[source];
      canvas.pixels[destination + 1] = referenceFrame.patch[source + 1];
      canvas.pixels[destination + 2] = referenceFrame.patch[source + 2];
      canvas.pixels[destination + 3] = referenceFrame.patch[source + 3];
    }
  }
}

function disposeReferenceFrame(canvas, beforeFrame, referenceFrame, background) {
  if (referenceFrame.disposalType === 2) {
    const bounds = clippedBounds(canvas.width, canvas.height, referenceFrame.dims);
    for (let y = bounds.y0; y < bounds.y1; y += 1) {
      for (let x = bounds.x0; x < bounds.x1; x += 1) {
        canvas.pixels.set(background, (y * canvas.width + x) * 4);
      }
    }
  } else if (referenceFrame.disposalType === 3) {
    canvas.pixels.set(beforeFrame);
  }
}

function compareLocalPixels(frame, referenceFrame, errors) {
  if (frame.indices.length !== referenceFrame.pixels.length) {
    errors.push(
      `frame ${frame.index}: index count ${frame.indices.length} != ${referenceFrame.pixels.length}`,
    );
    return 0;
  }

  let differingPixels = 0;
  for (let i = 0; i < frame.indices.length; i += 1) {
    if (frame.indices[i] !== referenceFrame.pixels[i]) {
      differingPixels += 1;
    }
  }
  if (differingPixels !== 0) {
    errors.push(`frame ${frame.index}: ${differingPixels} decoded color indices differ`);
  }

  const ownRgba = frameRgba(frame);
  let differingRgba = 0;
  for (let i = 0; i < ownRgba.length; i += 4) {
    const ownAlpha = ownRgba[i + 3];
    const referenceAlpha = referenceFrame.patch[i + 3];
    const alphaDiffers = (ownAlpha === 0) !== (referenceAlpha === 0);
    const rgbDiffers = ownAlpha !== 0 && (
      ownRgba[i] !== referenceFrame.patch[i]
      || ownRgba[i + 1] !== referenceFrame.patch[i + 1]
      || ownRgba[i + 2] !== referenceFrame.patch[i + 2]
    );
    if (alphaDiffers || rgbDiffers) {
      differingRgba += 1;
    }
  }
  if (differingRgba !== 0) {
    errors.push(`frame ${frame.index}: ${differingRgba} decoded RGBA pixels differ`);
  }
  return frame.indices.length;
}

function compareFrameMetadata(frame, referenceFrame, errors) {
  const expected = {
    left: frame.left,
    top: frame.top,
    width: frame.width,
    height: frame.height,
  };
  for (const [key, value] of Object.entries(expected)) {
    if (referenceFrame.dims[key] !== value) {
      errors.push(
        `frame ${frame.index}: ${key} ${referenceFrame.dims[key]} != ${value}`,
      );
    }
  }
  const referenceDisposal = referenceFrame.disposalType || 0;
  if (referenceDisposal !== frame.disposal) {
    errors.push(
      `frame ${frame.index}: disposal ${referenceDisposal} != ${frame.disposal}`,
    );
  }
  if (
    normalizedIndex(referenceFrame.transparentIndex)
    !== frame.transparentIndex
  ) {
    errors.push(
      `frame ${frame.index}: transparent index `
      + `${referenceFrame.transparentIndex} != ${frame.transparentIndex}`,
    );
  }
  if (frame.delay !== 0 && referenceFrame.delay !== frame.delay * 10) {
    errors.push(
      `frame ${frame.index}: delay ${referenceFrame.delay}ms != ${frame.delay * 10}ms`,
    );
  }
}

function compareReferenceCanvases(gif, parsed, referenceFrames, steps, errors) {
  const canvas = createReferenceCanvas(parsed);
  const background = referenceBackground(parsed);
  let statesCompared = 0;
  const rounds = referenceFrames.length === 0
    ? 0
    : Math.ceil(steps.length / referenceFrames.length);

  for (let round = 0; round < rounds; round += 1) {
    for (let frameIndex = 0; frameIndex < referenceFrames.length; frameIndex += 1) {
      const step = steps[round * referenceFrames.length + frameIndex];
      if (!step) {
        continue;
      }
      const referenceFrame = referenceFrames[frameIndex];
      const beforeFrame = new Uint8Array(canvas.pixels);
      paintReferenceFrame(canvas, referenceFrame);

      const drawnComparison = compareCanvases(
        gif.width,
        gif.height,
        step.drawn,
        canvas.pixels,
      );
      if (!drawnComparison.equal) {
        errors.push(
          `round ${round}, frame ${frameIndex}: drawn canvas differs in `
          + `${drawnComparison.differences} pixels`,
        );
      }
      statesCompared += 1;

      disposeReferenceFrame(canvas, beforeFrame, referenceFrame, background);
      const disposedComparison = compareCanvases(
        gif.width,
        gif.height,
        step.disposed,
        canvas.pixels,
      );
      if (!disposedComparison.equal) {
        errors.push(
          `round ${round}, frame ${frameIndex}: disposed canvas differs in `
          + `${disposedComparison.differences} pixels`,
        );
      }
      statesCompared += 1;
    }
  }
  return statesCompared;
}

function referenceLoopCount(parsed) {
  const application = parsed.frames
    .map((frame) => frame.application)
    .find((entry) => entry && entry.id === 'NETSCAPE2.0');
  if (!application || !application.blocks || application.blocks.length < 3) {
    return null;
  }
  return application.blocks[1] | (application.blocks[2] << 8);
}

function crossCheck(gif, buffer, steps) {
  const parsed = parseGIF(asArrayBuffer(buffer));
  const referenceFrames = decompressFrames(parsed, true);
  const errors = [];
  let localPixelsCompared = 0;

  if (referenceFrames.length !== gif.frames.length) {
    errors.push(
      `frame count ${referenceFrames.length} != ${gif.frames.length}`,
    );
  }
  if (parsed.lsd.width !== gif.width || parsed.lsd.height !== gif.height) {
    errors.push(
      `logical screen ${parsed.lsd.width}x${parsed.lsd.height} `
      + `!= ${gif.width}x${gif.height}`,
    );
  }
  if (parsed.lsd.backgroundColorIndex !== gif.backgroundIndex) {
    errors.push(
      `background index ${parsed.lsd.backgroundColorIndex} `
      + `!= ${gif.backgroundIndex}`,
    );
  }
  if (referenceLoopCount(parsed) !== gif.loopCount) {
    errors.push(
      `loop count ${referenceLoopCount(parsed)} != ${gif.loopCount}`,
    );
  }

  const count = Math.min(referenceFrames.length, gif.frames.length);
  for (let i = 0; i < count; i += 1) {
    compareFrameMetadata(gif.frames[i], referenceFrames[i], errors);
    localPixelsCompared += compareLocalPixels(
      gif.frames[i],
      referenceFrames[i],
      errors,
    );
  }

  const canvasStatesCompared = compareReferenceCanvases(
    gif,
    parsed,
    referenceFrames,
    steps,
    errors,
  );

  return {
    ok: errors.length === 0,
    library: 'gifuct-js',
    version: require('gifuct-js/package.json').version,
    framesCompared: count,
    localPixelsCompared,
    canvasStatesCompared,
    errors,
  };
}

module.exports = {
  crossCheck,
};
