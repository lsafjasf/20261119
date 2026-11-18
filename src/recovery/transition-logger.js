import { appendFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

function ensureDir(filePath) {
  mkdirSync(dirname(filePath), { recursive: true });
}

// Persists two append-only JSONL journals:
//   transitions.log  - every state machine migration
//   recovery.log     - one record per recovery attempt / termination
// A null path keeps the journal in memory only (handy for unit tests).
export class TransitionLogger {
  constructor({ transitionsPath = null, recoveryPath = null, clock = Date } = {}) {
    this.transitionsPath = transitionsPath;
    this.recoveryPath = recoveryPath;
    this.clock = clock;
    this.transitions = [];
    this.recoveryRecords = [];
    if (transitionsPath) ensureDir(transitionsPath);
    if (recoveryPath) ensureDir(recoveryPath);
  }

  logTransition(record) {
    const entry = { ts: this.clock.now(), ...record };
    this.transitions.push(entry);
    if (this.transitionsPath) {
      appendFileSync(this.transitionsPath, `${JSON.stringify(entry)}\n`);
    }
    return entry;
  }

  logRecovery(record) {
    const entry = { ts: this.clock.now(), ...record };
    this.recoveryRecords.push(entry);
    if (this.recoveryPath) {
      appendFileSync(this.recoveryPath, `${JSON.stringify(entry)}\n`);
    }
    return entry;
  }

  // Exactly one record per termination; the engine guards the call, and the
  // journal asserts the invariant as defence in depth.
  logTermination(record) {
    if (this.recoveryRecords.some((r) => r.event === "terminated")) {
      throw new Error("termination event already emitted");
    }
    return this.logRecovery({ event: "terminated", ...record });
  }
}
