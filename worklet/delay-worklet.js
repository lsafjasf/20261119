import { DelayEngine, TestSignalGenerator } from "../src/dsp.js";

const SCOPE_SIZE = 1024;
const REPORT_EVERY_BLOCKS = 8;

class FractionalDelayProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const config = options.processorOptions || {};
    this.engine = new DelayEngine({
      sampleRate,
      feedback: config.feedback ?? 0.35,
      mix: config.mix ?? 1,
      interpolation: config.interpolation || "cubic",
      crossfadeMs: config.crossfadeMs ?? 28,
    });
    this.engine.setDelayMs(config.delayMs ?? 23.4);
    this.generator = new TestSignalGenerator(sampleRate, config.source || "sine");
    this.paused = false;
    this.blockSerial = 0;
    this.blocksSinceReport = 0;
    this.scopeIn = new Float32Array(SCOPE_SIZE);
    this.scopeOut = new Float32Array(SCOPE_SIZE);
    this.scopeCursor = 0;
    this.inputPeak = 0;
    this.outputPeak = 0;
    this.port.onmessage = (event) => this.handleMessage(event.data);
  }

  handleMessage(message) {
    if (!message || typeof message.type !== "string") return;
    if (message.type === "pause") {
      const shouldPause = Boolean(message.paused);
      if (this.paused !== shouldPause) {
        this.paused = shouldPause;
        if (!this.paused) this.engine.flush();
      }
      this.postTelemetry();
      return;
    }
    if (message.type === "flush") {
      this.engine.flush();
      return;
    }
    if (message.type === "source") {
      this.generator.setMode(message.source);
      return;
    }
    if (message.type === "interpolation") {
      this.engine.setInterpolation(message.interpolation);
      return;
    }
    if (message.type === "feedback") {
      this.engine.setFeedback(message.value);
      return;
    }
    if (message.type === "mix") {
      this.engine.setMix(message.value);
      return;
    }
    if (message.type === "crossfade") {
      this.engine.setCrossfadeMs(message.value);
      return;
    }
    if (message.type === "delay") {
      this.engine.setDelayMs(message.value);
    }
  }

  process(inputs, outputs) {
    const output = outputs[0];
    if (!output || !output[0]) return true;
    const length = output[0].length;
    const left = output[0];
    const right = output[1] || null;

    if (this.paused) {
      left.fill(0);
      if (right) right.fill(0);
      return true;
    }

    const inPeak = this.inputPeak;
    const outPeak = this.outputPeak;
    for (let index = 0; index < length; index += 1) {
      const input = this.generator.next();
      const processed = this.engine.processSample(input);
      left[index] = processed;
      if (right) right[index] = processed;
      this.inputPeak = Math.max(this.inputPeak, Math.abs(input));
      this.outputPeak = Math.max(this.outputPeak, Math.abs(processed));
      this.scopeIn[this.scopeCursor] = input;
      this.scopeOut[this.scopeCursor] = processed;
      this.scopeCursor = (this.scopeCursor + 1) % SCOPE_SIZE;
    }
    this.blockSerial += 1;
    this.blocksSinceReport += 1;
    if (this.blocksSinceReport >= REPORT_EVERY_BLOCKS) {
      this.blocksSinceReport = 0;
      this.postTelemetry(length);
    }
    return true;
  }

  postTelemetry() {
    const inPeak = this.inputPeak;
    const outPeak = this.outputPeak;
    this.inputPeak = 0;
    this.outputPeak = 0;
    this.port.postMessage({
      type: "telemetry",
      ...this.engine.telemetry(),
      paused: this.paused,
      blockSerial: this.blockSerial,
      sourceSample: this.generator.time,
      scopeCursor: this.scopeCursor,
      inputPeak: inPeak,
      outputPeak: outPeak,
      scopeIn: new Float32Array(this.scopeIn),
      scopeOut: new Float32Array(this.scopeOut),
    });
  }
}

registerProcessor("fractional-delay", FractionalDelayProcessor);
