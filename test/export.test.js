import assert from "node:assert/strict";
import test from "node:test";
import { makeConstantToneSource } from "../src/dsp-utils.js";
import { renderGranularPcm, pcmToStereoWav } from "../src/offline-render.js";
import { encodePcm16Wav } from "../src/wav.js";

const RATE = 48000;

function ascii(view, offset, length) {
  let text = "";
  for (let i = 0; i < length; i += 1) text += String.fromCharCode(view.getUint8(offset + i));
  return text;
}

test("offline render exports non-silent synthesized grains", async () => {
  const source = new Float32Array(RATE * 0.2);
  source[2400] = 0.9;
  const result = await renderGranularPcm({
    source,
    sampleRate: RATE,
    durationSeconds: 0.2,
    settings: {
      grainLengthSeconds: 0.08,
      emissionIntervalSeconds: 0.25,
      windowType: "rect",
      maxVoices: 8,
      masterGain: 1,
      scanRate: 0,
      clipEnabled: false,
      readPositionSeconds: 2400 / RATE,
      frozen: true
    }
  });

  assert.equal(result.pcm.length, RATE * 0.2);
  assert.ok(Math.abs(result.pcm[0] - 0.9) < 1e-6);
  assert.ok(result.pcm.some((sample) => Math.abs(sample) > 0.5));
  assert.equal(result.status.accepted, 1);
  assert.equal(result.status.rejected, 0);
});

test("offline render preserves max voice and reject-new accounting", async () => {
  const source = makeConstantToneSource(RATE, 0.4);
  const result = await renderGranularPcm({
    source,
    sampleRate: RATE,
    durationSeconds: 0.22,
    settings: {
      grainLengthSeconds: 0.04,
      emissionIntervalSeconds: 0.003,
      windowType: "hann",
      maxVoices: 4,
      masterGain: 0.5,
      scanRate: 0,
      readPositionSeconds: 0.1,
      frozen: true
    }
  });

  assert.ok(result.status.peakActive <= 4);
  assert.ok(result.status.rejected > 0);
  assert.equal(result.status.queueLength, 0);
  assert.equal(result.status.attempts, result.status.accepted + result.status.rejected);
});

test("PCM16 WAV contains valid stereo header and the rendered samples", () => {
  const left = Float32Array.from([0, 0.5, -0.5, 1, -1]);
  const right = Float32Array.from([0, -0.25, 0.25, 0.75, -0.75]);
  const buffer = encodePcm16Wav([left, right], RATE);
  const view = new DataView(buffer);

  assert.equal(ascii(view, 0, 4), "RIFF");
  assert.equal(ascii(view, 8, 4), "WAVE");
  assert.equal(ascii(view, 12, 4), "fmt ");
  assert.equal(view.getUint16(20, true), 1, "PCM format");
  assert.equal(view.getUint16(22, true), 2, "stereo");
  assert.equal(view.getUint32(24, true), RATE);
  assert.equal(view.getUint16(34, true), 16);
  assert.equal(ascii(view, 36, 4), "data");
  assert.equal(view.getUint32(40, true), 5 * 2 * 2);

  assert.equal(view.getInt16(44, true), 0);
  assert.equal(view.getInt16(46, true), 0);
  assert.equal(view.getInt16(48, true), Math.round(0.5 * 0x7fff));
  assert.equal(view.getInt16(50, true), Math.round(-0.25 * 0x8000));
});

test("stereo export helper writes the same synthesized PCM to both channels", async () => {
  const source = new Float32Array(RATE * 0.1).fill(0.3);
  const result = await renderGranularPcm({
    source,
    sampleRate: RATE,
    durationSeconds: 0.05,
    settings: {
      grainLengthSeconds: 0.05,
      emissionIntervalSeconds: 0.025,
      windowType: "hann",
      maxVoices: 4,
      masterGain: 1,
      scanRate: 0,
      frozen: true,
      readPositionSeconds: 0
    }
  });
  const wav = pcmToStereoWav(result.pcm, RATE);
  const view = new DataView(wav);
  assert.equal(view.getUint32(40, true), result.pcm.length * 4);
  const left = view.getInt16(44, true);
  const right = view.getInt16(46, true);
  assert.equal(left, right);
});
