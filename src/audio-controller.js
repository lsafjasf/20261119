import { GranularEngine } from "./granular-engine.js";
import { resampleLinear } from "./dsp-utils.js";

export class RealtimeGranularAudio {
  constructor(options = {}) {
    this.onStatus = options.onStatus || (() => {});
    this.onError = options.onError || (() => {});
    this.context = null;
    this.mode = "pending";
    this.node = null;
    this.fallbackEngine = null;
    this.source = new Float32Array(0);
    this.sourceRate = 48000;
    this.settings = {
      grainLengthSeconds: 0.09,
      emissionIntervalSeconds: 0.03,
      windowType: "hann",
      maxVoices: 32,
      masterGain: 0.8,
      scanRate: 1,
      clipEnabled: true
    };
    this.playing = false;
    this.pollTimer = 0;
  }

  get sampleRate() {
    return this.context ? this.context.sampleRate : 48000;
  }

  async ensureContext() {
    if (!this.context) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) throw new Error("This browser does not support Web Audio.");
      this.context = new AudioContextClass({ latencyHint: "interactive" });
    }
    if (this.context.state === "suspended") await this.context.resume();
    if (!this.node) await this.createNode();
  }

  async createNode() {
    try {
      await this.context.audioWorklet.addModule(new URL("./granular-worklet.js", import.meta.url));
      this.node = new AudioWorkletNode(this.context, "granular-processor", {
        numberOfInputs: 0,
        numberOfOutputs: 1,
        outputChannelCount: [2]
      });
      this.node.port.onmessage = (event) => {
        if (event.data?.type === "state") this.onStatus(event.data.status);
      };
      this.node.connect(this.context.destination);
      this.mode = "worklet";
      if (this.source.length) {
        this.node.port.postMessage({ type: "source", source: this.getResampledSource() });
        this.applySettings();
      }
    } catch (error) {
      this.onError(`AudioWorklet unavailable, using compatibility mode: ${error.message}`);
      this.createFallbackNode();
      this.mode = "script";
    }
  }

  createFallbackNode() {
    this.fallbackEngine = new GranularEngine({ sampleRate: this.context.sampleRate });
    this.fallbackEngine.configure(this.settings);
    this.fallbackEngine.setSource(this.getResampledSource());
    this.node = this.context.createScriptProcessor(1024, 0, 2);
    this.node.onaudioprocess = (event) => {
      const output = event.outputBuffer;
      const left = output.getChannelData(0);
      const right = output.getChannelData(1);
      this.fallbackEngine.process(left, left.length);
      right.set(left);
    };
    this.node.connect(this.context.destination);
  }

  getResampledSource() {
    return resampleLinear(this.source, this.sourceRate, this.sampleRate);
  }

  async setSource(source, sourceRate) {
    this.source = Float32Array.from(source);
    this.sourceRate = sourceRate || 48000;
    if (!this.context) return;
    await this.ensureContext();
    const resampled = this.getResampledSource();
    if (this.mode === "worklet") {
      this.node.port.postMessage({ type: "source", source: resampled });
      this.applySettings();
    } else {
      this.fallbackEngine.setSource(resampled);
    }
  }

  configure(settings) {
    this.settings = { ...this.settings, ...settings };
    if (!this.context) return;
    if (this.mode === "worklet") {
      this.node.port.postMessage({ type: "config", settings: this.settings });
    } else if (this.fallbackEngine) {
      this.fallbackEngine.configure(settings);
    }
  }

  applySettings() {
    if (this.mode === "worklet") {
      this.node.port.postMessage({ type: "config", settings: this.settings });
    }
  }

  async play() {
    await this.ensureContext();
    this.playing = true;
    if (this.mode === "worklet") {
      this.applySettings();
      this.node.port.postMessage({
        type: "seek",
        seconds: this.settings.readPositionSeconds || 0
      });
      this.node.port.postMessage({ type: "freeze", frozen: Boolean(this.settings.frozen) });
      this.node.port.postMessage({ type: "play" });
    } else {
      this.fallbackEngine.seek(this.settings.readPositionSeconds || 0);
      this.fallbackEngine.setFrozen(Boolean(this.settings.frozen));
      this.fallbackEngine.play();
    }
    this.startPolling();
    return this.mode;
  }

  stop() {
    this.playing = false;
    if (this.mode === "worklet") {
      this.node.port.postMessage({ type: "stop" });
      this.requestState();
    } else if (this.fallbackEngine) {
      this.fallbackEngine.stop();
      this.onStatus(this.fallbackEngine.getStatus());
    }
  }

  setFrozen(frozen) {
    this.settings.frozen = frozen;
    if (this.mode === "worklet") {
      this.node.port.postMessage({ type: "freeze", frozen });
    } else if (this.fallbackEngine) {
      this.fallbackEngine.setFrozen(frozen);
      this.onStatus(this.fallbackEngine.getStatus());
    }
  }

  seek(seconds) {
    this.settings.readPositionSeconds = seconds;
    if (this.mode === "worklet") {
      this.node.port.postMessage({ type: "seek", seconds });
    } else if (this.fallbackEngine) {
      this.fallbackEngine.seek(seconds);
      this.onStatus(this.fallbackEngine.getStatus());
    }
  }

  requestState() {
    if (this.mode === "worklet") {
      this.node.port.postMessage({ type: "get-state" });
    } else if (this.fallbackEngine) {
      this.onStatus(this.fallbackEngine.getStatus());
    }
  }

  startPolling() {
    if (this.pollTimer) return;
    this.pollTimer = window.setInterval(() => this.requestState(), 50);
  }
}
