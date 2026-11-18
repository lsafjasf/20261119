import test from 'node:test';
import assert from 'node:assert/strict';

class FakeClassList {
  constructor() {
    this.values = new Set();
  }

  add(...names) {
    names.forEach((name) => this.values.add(name));
  }

  remove(...names) {
    names.forEach((name) => this.values.delete(name));
  }

  toggle(name, force) {
    const enabled = force === undefined ? !this.values.has(name) : force;
    if (enabled) this.values.add(name);
    else this.values.delete(name);
    return enabled;
  }

  contains(name) {
    return this.values.has(name);
  }
}

class FakeElement {
  constructor(id = '') {
    this.id = id;
    this.children = [];
    this.classList = new FakeClassList();
    this.dataset = {};
    this.style = {};
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.textContent = '';
    this.width = 0;
    this.height = 0;
    this.src = '';
    this.href = '';
    this.listeners = new Map();
  }

  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }

  dispatch(type) {
    const event = { target: this, key: '', preventDefault() {} };
    (this.listeners.get(type) ?? []).forEach((handler) => handler(event));
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = children;
  }

  closest() {
    return new FakeElement('control-section');
  }

  getBoundingClientRect() {
    if (this.id === 'polarCanvas') return { width: 720, height: 440 };
    if (this.id === 'arrayCanvas') return { width: 152, height: 152 };
    return { width: 360, height: 64 };
  }

  getContext() {
    const gradient = { addColorStop() {} };
    const noop = () => {};
    return new Proxy({
      createRadialGradient: () => gradient,
      createLinearGradient: () => gradient,
      measureText: () => ({ width: 20 })
    }, {
      get(target, property) {
        return property in target ? target[property] : noop;
      },
      set(target, property, value) {
        target[property] = value;
        return true;
      }
    });
  }
}

function installBrowserStubs() {
  const elements = new Map();
  const ids = [
    'runButton', 'runStatus', 'runTime', 'estimateAngle', 'estimateFoot',
    'angleError', 'angleErrorFoot', 'confidenceValue', 'confidenceMeter',
    'confidenceFoot', 'contrastValue', 'contrastFoot', 'peakList', 'peakCount',
    'mappingStatus', 'mappingMessage', 'mappingResidual', 'mappingRecommendation',
    'coordinateCount', 'coordinateList', 'symmetryChip', 'symmetryLead',
    'symmetryNote', 'rotationPeriod', 'mirrorAxes', 'globalAmbiguity', 'steerAngle',
    'rawAudio', 'enhancedAudio', 'rawDownload', 'enhancedDownload', 'rawRms',
    'enhancedRms', 'toast', 'polarCanvas', 'arrayCanvas', 'rawWaveCanvas',
    'enhancedWaveCanvas', 'sourceAAngle', 'sourceBAngle', 'sourceAAngleValue',
    'sourceBAngleValue', 'sourceBControl', 'reverbEnabled', 'reverbAmount',
    'reverbAmountValue', 'reverbSliderRow', 'micCount', 'arrayRadius', 'soundSpeed',
    'apertureLabel', 'footerMicCount', 'footerSoundSpeed', 'swapDataButton',
    'remapButton', 'restoreButton'
  ];
  ids.forEach((id) => elements.set(id, new FakeElement(id)));

  elements.get('sourceAAngle').value = '37';
  elements.get('sourceBAngle').value = '138';
  elements.get('reverbEnabled').checked = true;
  elements.get('reverbAmount').value = '38';
  elements.get('micCount').value = '8';
  elements.get('arrayRadius').value = '12';
  elements.get('soundSpeed').value = '343';

  const segments = ['single', 'dual'].map((mode) => {
    const segment = new FakeElement();
    segment.dataset.sourceMode = mode;
    return segment;
  });

  globalThis.window = globalThis;
  globalThis.devicePixelRatio = 1;
  globalThis.addEventListener = () => {};
  globalThis.requestAnimationFrame = (callback) => setTimeout(callback, 0);
  globalThis.document = {
    querySelector(selector) {
      const id = selector.replace(/^#/, '');
      if (!elements.has(id)) elements.set(id, new FakeElement(id));
      return elements.get(id);
    },
    querySelectorAll(selector) {
      return selector === '.segment' ? segments : [];
    },
    createElement() {
      return new FakeElement();
    },
    createTextNode(text) {
      return { textContent: text };
    }
  };
  globalThis.URL.createObjectURL = () => 'blob:smoke-test';
  globalThis.URL.revokeObjectURL = () => {};
  return elements;
}

test('page module initializes DSP, plots and audio without runtime errors', async () => {
  const elements = installBrowserStubs();
  const errors = [];
  const onError = (error) => errors.push(error);
  process.on('uncaughtException', onError);
  process.on('unhandledRejection', onError);

  await import(`../js/app.js?smoke=${Date.now()}`);
  await new Promise((resolve) => setTimeout(resolve, 650));

  assert.notEqual(elements.get('estimateAngle').textContent, '--');
  assert.ok(elements.get('peakList').children.length > 0);
  assert.match(elements.get('mappingStatus').textContent, /映射/);
  assert.equal(elements.get('rawAudio').src, 'blob:smoke-test');
  assert.equal(elements.get('enhancedAudio').src, 'blob:smoke-test');

  elements.get('swapDataButton').dispatch('click');
  await new Promise((resolve) => setTimeout(resolve, 350));
  assert.match(elements.get('mappingStatus').textContent, /异常/);
  assert.match(elements.get('mappingRecommendation').textContent, /CH1/);

  elements.get('remapButton').dispatch('click');
  await new Promise((resolve) => setTimeout(resolve, 350));
  assert.match(elements.get('mappingStatus').textContent, /重映射/);

  process.off('uncaughtException', onError);
  process.off('unhandledRejection', onError);
  assert.deepEqual(errors, []);
});
