// Headless smoke test for the UI controller using a minimal DOM shim.
// Canvas 2D calls are stubbed; the reassignment/metrics pipeline is real.

import { describe, it, assert } from './harness.js';

function makeCanvas() {
  return {
    clientWidth: 800,
    clientHeight: 340,
    width: 0,
    height: 0,
    listeners: {},
    getContext: () => ({
      setTransform() {}, clearRect() {}, fillRect() {}, beginPath() {},
      moveTo() {}, lineTo() {}, stroke() {}, fillText() {}, drawImage() {},
      strokeRect() {}, createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
      putImageData() {}
    }),
    getBoundingClientRect: () => ({ left: 0, top: 0 }),
    addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); },
    dispatch(type, evt) { (this.listeners[type] ?? []).forEach((fn) => fn(evt)); }
  };
}

function makeSelect() {
  return { value: '', options: [], appendChild() {}, listeners: {},
    addEventListener(t, fn) { (this.listeners[t] ??= []).push(fn); },
    dispatch(t, e) { (this.listeners[t] ?? []).forEach((fn) => fn(e)); } };
}

function installDomShim() {
 const elements = {
    'stft-canvas': makeCanvas(),
    'reassigned-canvas': makeCanvas(),
    'signal-select': makeSelect(),
    'window-select': makeSelect(),
    recompute: { addEventListener() {} },
    inspector: { innerHTML: '', classList: { add() {} } },
    stats: { innerHTML: '' }
  };
  globalThis.performance = { now: () => Date.now() };
  globalThis.window = {
    devicePixelRatio: 1,
    addEventListener() {},
    app: null
  };
  globalThis.document = {
    getElementById: (id) => elements[id],
    createElement: () => ({
      width: 0,
      height: 0,
      getContext: () => ({
        createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
        putImageData() {}
      })
    })
  };
  return elements;
}

export async function runAppSmokeTests() {
  describe('UI controller smoke test', () => {
    it('boots, renders, switches windows with fixed units, and inspects a cell', async () => {
      const els = installDomShim();
      const { AnalyzerApp } = await import('../js/app.js');
      const app = new AnalyzerApp();
      assert(app.result, 'analysis runs on boot');
      const first = app.result;
      const firstDuration = (first.params.frames * first.params.hop) / first.params.sampleRate;
      const firstNyquist = first.params.sampleRate / 2;

      // Change window length; physical units (seconds span, Nyquist Hz) persist.
      els['window-select'].value = '2048';
      els['window-select'].dispatch('change', {});
      const second = app.result;
      const secondDuration = (second.params.frames * second.params.hop) / second.params.sampleRate;
      assert(second.params.nfft === 2048, 'window length updated');
      assert(Math.abs(second.params.sampleRate / 2 - firstNyquist) < 1e-9, 'frequency unit (Hz) preserved');
      assert(Math.abs(secondDuration - firstDuration) < 0.05, 'time axis stays in seconds over the same signal');

      // Simulate clicking a kept cell on the STFT canvas.
      const idx = first.status.indexOf(0);
      const frame = Math.floor(idx / first.params.halfBins);
      const bin = idx % first.params.halfBins;
      app.inspect({ frame, bin, time: 0, freq: 0 });
      assert(els.inspector.innerHTML.includes('Reassigned time'), 'inspector shows reassigned coordinates');
      assert(els.inspector.innerHTML.includes('Original time'), 'inspector shows original coordinates');

      // Click a low-energy cell and confirm a discard reason is rendered.
      const lowIdx = first.status.indexOf(1);
      if (lowIdx >= 0) {
        app.inspect({ frame: Math.floor(lowIdx / first.params.halfBins), bin: lowIdx % first.params.halfBins, time: 0, freq: 0 });
        assert(els.inspector.innerHTML.includes('Discard reason'), 'discard reason shown for low-energy cell');
      }
      assert(els.stats.innerHTML.includes('Energy accounting'), 'sidebar renders accounting stats');
    });
  });
}
