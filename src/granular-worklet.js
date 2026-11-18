import { GranularEngine } from "./granular-engine.js";

class GranularProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.engine = new GranularEngine({ sampleRate });
    this.mono = new Float32Array(128);
    this.port.onmessage = (event) => this.handleMessage(event.data);
  }

  handleMessage(message) {
    if (!message || typeof message.type !== "string") return;
    switch (message.type) {
      case "source":
        this.engine.setSource(message.source);
        break;
      case "config":
        this.engine.configure(message.settings || {});
        break;
      case "play":
        this.engine.play();
        break;
      case "stop":
        this.engine.stop();
        break;
      case "freeze":
        this.engine.setFrozen(message.frozen);
        break;
      case "seek":
        this.engine.seek(message.seconds);
        break;
      case "reset-counters":
        this.engine.resetCounters();
        break;
      case "get-state":
        this.port.postMessage({ type: "state", status: this.engine.getStatus() });
        break;
      default:
        break;
    }
  }

  process(_inputs, outputs) {
    const output = outputs[0];
    if (!output || !output.length) return true;
    const frames = output[0].length;
    if (this.mono.length !== frames) this.mono = new Float32Array(frames);
    this.engine.process(this.mono, frames);
    for (let channel = 0; channel < output.length; channel += 1) {
      output[channel].set(this.mono);
    }
    return true;
  }
}

registerProcessor("granular-processor", GranularProcessor);
