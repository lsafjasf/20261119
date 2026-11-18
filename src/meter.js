export class BufferMeter {
  constructor() {
    this.heldBytes = 0;
    this.peakHeldBytes = 0;
    this.totalReadBytes = 0;
    this.maxHeldBlocks = 0;
  }

  add(bytes, blocks = 0) {
    this.heldBytes += bytes;
    this.totalReadBytes += bytes;
    this.peakHeldBytes = Math.max(this.peakHeldBytes, this.heldBytes);
    this.maxHeldBlocks = Math.max(this.maxHeldBlocks, blocks);
  }

  remove(bytes) {
    this.heldBytes = Math.max(0, this.heldBytes - bytes);
  }

  resetPeak() {
    this.peakHeldBytes = this.heldBytes;
    this.maxHeldBlocks = 0;
  }

  snapshot() {
    return {
      heldBytes: this.heldBytes,
      peakHeldBytes: this.peakHeldBytes,
      totalReadBytes: this.totalReadBytes,
      maxHeldBlocks: this.maxHeldBlocks,
    };
  }
}

