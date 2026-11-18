'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { PNG } = require('pngjs');

function ensureDirectory(directory) {
  fs.mkdirSync(directory, { recursive: true });
}

function relativePath(filePath) {
  return path.relative(process.cwd(), filePath).split(path.sep).join('/');
}

function saveCanvasPng(filePath, width, height, pixels) {
  ensureDirectory(path.dirname(filePath));
  const png = new PNG({ width, height });
  png.data = Buffer.from(pixels);
  fs.writeFileSync(filePath, PNG.sync.write(png));
}

function hslToRgb(hue, saturation, lightness) {
  const c = (1 - Math.abs(2 * lightness - 1)) * saturation;
  const x = c * (1 - Math.abs(((hue / 60) % 2) - 1));
  const m = lightness - c / 2;
  let rgb;
  if (hue < 60) rgb = [c, x, 0];
  else if (hue < 120) rgb = [x, c, 0];
  else if (hue < 180) rgb = [0, c, x];
  else if (hue < 240) rgb = [0, x, c];
  else if (hue < 300) rgb = [x, 0, c];
  else rgb = [c, 0, x];
  return rgb.map((channel) => Math.round((channel + m) * 255));
}

function frameColor(frameIndex) {
  if (frameIndex < 0) {
    return [48, 52, 58];
  }
  return hslToRgb((frameIndex * 137.508) % 360, 0.72, 0.56);
}

function countColor(count, maximum) {
  if (count === 0) {
    return [30, 34, 40];
  }
  const ratio = maximum <= 1 ? 1 : (count - 1) / (maximum - 1);
  return [
    Math.round(25 + 230 * ratio),
    Math.round(70 + 120 * ratio),
    Math.round(170 - 110 * ratio),
  ];
}

function fillColor(target, pixelIndex, color) {
  target.set([color[0], color[1], color[2], 255], pixelIndex * 4);
}

function contactImages(gif, ledger) {
  const tablePixels = new Uint8Array(gif.width * gif.height * 4);
  const countPixels = new Uint8Array(gif.width * gif.height * 4);
  let maximumCount = 0;
  for (const count of ledger.counts) {
    maximumCount = Math.max(maximumCount, count);
  }
  for (let i = 0; i < ledger.lastFrame.length; i += 1) {
    fillColor(tablePixels, i, frameColor(ledger.lastFrame[i]));
    fillColor(countPixels, i, countColor(ledger.counts[i], maximumCount));
  }
  return { tablePixels, countPixels };
}

function writeGridCsv(filePath, width, height, entries) {
  ensureDirectory(path.dirname(filePath));
  const lines = [];
  for (let y = 0; y < height; y += 1) {
    const row = [];
    for (let x = 0; x < width; x += 1) {
      row.push(entries[y * width + x]);
    }
    lines.push(row.join(','));
  }
  fs.writeFileSync(filePath, `${lines.join('\n')}\n`);
}

function writeContactArtifacts(outDir, baseName, gif, ledger, title) {
  const tableCsv = path.join(outDir, `contact_table_${baseName}.csv`);
  const framesCsv = path.join(outDir, `contact_frames_${baseName}.csv`);
  const tablePng = path.join(outDir, `contact_table_${baseName}.png`);
  const countPng = path.join(outDir, `contact_count_${baseName}.png`);

  writeGridCsv(
    tableCsv,
    gif.width,
    gif.height,
    Array.from(ledger.lastFrame),
  );
  writeGridCsv(
    framesCsv,
    gif.width,
    gif.height,
    ledger.frameSets.map((set) => [...set].sort((a, b) => a - b).join('|')),
  );

  const images = contactImages(gif, ledger);
  saveCanvasPng(tablePng, gif.width, gif.height, images.tablePixels);
  saveCanvasPng(countPng, gif.width, gif.height, images.countPixels);

  return {
    title,
    tableCsv,
    framesCsv,
    tablePng,
    countPng,
  };
}

module.exports = {
  ensureDirectory,
  frameColor,
  relativePath,
  saveCanvasPng,
  writeContactArtifacts,
  writeGridCsv,
};
