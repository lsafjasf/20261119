const OPEN_STATES = new Set(["live", "in_transit"]);

export class FrameOwnershipError extends Error {
  constructor(message, details) {
    super(message);
    this.name = "FrameOwnershipError";
    this.details = details;
  }
}

export class VideoFrameOwnershipTracker {
  #sequence = 0;
  #frameToRecord = new WeakMap();
  #records = new Map();
  #events = [];
  #diagnostics = [];
  #stages = [];
  #scenarioId;
  #idPrefix;
  #clock;
  #reportedUnreleased = new Set();

  constructor({ scenarioId = "scenario", idPrefix = "VF", clock = () => Date.now() } = {}) {
    this.#scenarioId = scenarioId;
    this.#idPrefix = idPrefix;
    this.#clock = clock;
  }

  #nextFrameId() {
    this.#sequence += 1;
    return `${this.#idPrefix}-${String(this.#sequence).padStart(3, "0")}`;
  }

  #assertFrame(frame) {
    if (frame === null || (typeof frame !== "object" && typeof frame !== "function")) {
      throw new FrameOwnershipError("A VideoFrame-like object is required", {
        scenarioId: this.#scenarioId,
      });
    }
  }

  #recordFor(frame, { stage, operation }) {
    const record = this.#frameToRecord.get(frame);
    if (!record) {
      throw new FrameOwnershipError(`Cannot ${operation} an unregistered frame`, {
        scenarioId: this.#scenarioId,
        stage,
        operation,
      });
    }
    return record;
  }

  #emit(record, type, payload = {}) {
    const event = {
      eventIndex: this.#events.length,
      at: this.#clock(),
      scenarioId: this.#scenarioId,
      type,
      frameId: record?.id ?? null,
      stage: payload.stage ?? record?.lastStage ?? null,
      owner: payload.owner ?? record?.owner ?? null,
      ...payload,
    };
    this.#events.push(event);
    if (record) {
      record.lastStage = event.stage;
      record.events.push(event);
    }
    return event;
  }

  #warn(record, code, { stage, message, details = {} }) {
    const diagnostic = {
      code,
      severity: code === "use-after-close" || code === "double-close" ? "error" : "warning",
      scenarioId: this.#scenarioId,
      frameId: record?.id ?? details.frameId ?? null,
      stage,
      eventIndex: this.#events.length,
      message,
      details,
    };
    this.#diagnostics.push(diagnostic);
    this.#emit(record, "warning", {
      stage,
      code,
      severity: diagnostic.severity,
      diagnosticIndex: this.#diagnostics.length - 1,
    });
    return diagnostic;
  }

  create(frame, { stage = "created", owner = "main", label, parentId = null, metadata = {} } = {}) {
    this.#assertFrame(frame);
    if (this.#frameToRecord.has(frame)) {
      throw new FrameOwnershipError("Frame is already tracked", {
        scenarioId: this.#scenarioId,
        stage,
      });
    }
    const id = this.#nextFrameId();
    const record = {
      id,
      ref: new WeakRef(frame),
      parentId,
      label: label ?? id,
      state: "live",
      owner,
      lastStage: stage,
      createdAt: this.#clock(),
      closedAt: null,
      closeCount: 0,
      closeAttempts: 0,
      lastUseStage: null,
      metadata: { ...metadata },
      events: [],
    };
    this.#frameToRecord.set(frame, record);
    this.#records.set(id, record);
    this.#emit(record, "create", { stage, owner, parentId, label: record.label });
    return id;
  }

  clone(sourceFrame, clonedFrame, { stage = "cloned", owner, label, metadata = {} } = {}) {
    this.#assertFrame(clonedFrame);
    const source = this.#recordFor(sourceFrame, { stage, operation: "clone" });
    if (source.state !== "live") {
      this.#warn(source, "use-after-close", {
        stage,
        message: `Frame ${source.id} was cloned while ${source.state}`,
        details: { operation: "clone" },
      });
      throw new FrameOwnershipError(`Cannot clone ${source.id} while ${source.state}`, {
        scenarioId: this.#scenarioId,
        frameId: source.id,
        stage,
      });
    }
    if (this.#frameToRecord.has(clonedFrame)) {
      throw new FrameOwnershipError("Cloned frame is already tracked", {
        scenarioId: this.#scenarioId,
        stage,
      });
    }
    const id = this.#nextFrameId();
    const record = {
      id,
      ref: new WeakRef(clonedFrame),
      parentId: source.id,
      label: label ?? id,
      state: "live",
      owner: owner ?? source.owner,
      lastStage: stage,
      createdAt: this.#clock(),
      closedAt: null,
      closeCount: 0,
      closeAttempts: 0,
      lastUseStage: null,
      metadata: { ...metadata },
      events: [],
    };
    this.#frameToRecord.set(clonedFrame, record);
    this.#records.set(id, record);
    this.#emit(record, "clone", {
      stage,
      owner: record.owner,
      parentId: source.id,
      label: record.label,
    });
    return id;
  }

  transferOut(frame, { from, to, stage = "transfer:posted", channel = "worker" } = {}) {
    const record = this.#recordFor(frame, { stage, operation: "transfer" });
    if (record.state !== "live") {
      this.#warn(record, "invalid-transfer", {
        stage,
        message: `Frame ${record.id} cannot transfer from state ${record.state}`,
        details: { from: record.owner, to, channel },
      });
      return false;
    }
    const sourceOwner = from ?? record.owner;
    record.state = "in_transit";
    record.owner = to;
    record.lastStage = stage;
    this.#emit(record, "transfer-out", {
      stage,
      from: sourceOwner,
      to,
      channel,
      owner: to,
    });
    return true;
  }

  acceptTransfer(frameId, frame, { stage = "transfer:accepted", channel = "worker" } = {}) {
    this.#assertFrame(frame);
    const record = this.#records.get(frameId);
    if (!record) {
      throw new FrameOwnershipError(`Unknown frame id ${frameId}`, {
        scenarioId: this.#scenarioId,
        stage,
      });
    }
    if (record.state !== "in_transit") {
      this.#warn(record, "invalid-transfer", {
        stage,
        message: `Frame ${record.id} cannot be accepted from state ${record.state}`,
        details: { to: record.owner, channel },
      });
      return false;
    }
    const oldFrame = record.ref.deref();
    if (oldFrame && oldFrame !== frame) {
      this.#frameToRecord.delete(oldFrame);
    }
    record.ref = new WeakRef(frame);
    record.state = "live";
    record.lastStage = stage;
    this.#frameToRecord.set(frame, record);
    this.#emit(record, "transfer-in", {
      stage,
      to: record.owner,
      channel,
      owner: record.owner,
    });
    return true;
  }

  use(frame, { stage = "use", operation = "process" } = {}) {
    const record = this.#recordFor(frame, { stage, operation });
    if (record.state === "closed") {
      record.lastUseStage = stage;
      this.#warn(record, "use-after-close", {
        stage,
        message: `Frame ${record.id} was used for ${operation} after close`,
        details: { operation },
      });
      return false;
    }
    if (record.state === "in_transit") {
      this.#warn(record, "use-while-in-transit", {
        stage,
        message: `Frame ${record.id} was used for ${operation} while in transit to ${record.owner}`,
        details: { operation, owner: record.owner },
      });
      return false;
    }
    record.lastUseStage = stage;
    this.#emit(record, "use", { stage, operation, owner: record.owner });
    return true;
  }

  close(frame, { stage = "close", reason = "released" } = {}) {
    const record = this.#recordFor(frame, { stage, operation: "close" });
    record.closeAttempts += 1;
    if (record.state === "closed") {
      this.#warn(record, "double-close", {
        stage,
        message: `Frame ${record.id} was closed again after close at ${record.closedStage}`,
        details: {
          operation: "close",
          previousCloseStage: record.closedStage,
          previousCloseAt: record.closedAt,
          closeAttempts: record.closeAttempts,
        },
      });
      return false;
    }
    frame.close();
    record.state = "closed";
    record.closeCount += 1;
    record.closedAt = this.#clock();
    record.closedStage = stage;
    record.lastStage = stage;
    this.#emit(record, "close", { stage, owner: record.owner, reason });
    return true;
  }

  closeById(frameId, { stage = "close-by-id", reason = "cleanup" } = {}) {
    const record = this.#records.get(frameId);
    if (!record) {
      return false;
    }
    const frame = record.ref.deref();
    if (!frame) {
      this.#warn(record, "frame-unreachable", {
        stage,
        message: `Frame ${frameId} could not be closed because its weak reference was released`,
        details: { reason },
      });
      return false;
    }
    return this.close(frame, { stage, reason });
  }

  inspectUnreleased({ stage = "finalize", reason = "not closed" } = {}) {
    const open = [...this.#records.values()].filter((record) => OPEN_STATES.has(record.state));
    for (const record of open) {
      const warningKey = `${record.id}@${stage}`;
      if (this.#reportedUnreleased.has(warningKey)) {
        continue;
      }
      this.#reportedUnreleased.add(warningKey);
      this.#warn(record, "unreleased-frame", {
        stage,
        message: `Frame ${record.id} is still ${record.state} on ${record.owner}`,
        details: {
          owner: record.owner,
          state: record.state,
          label: record.label,
          lastStage: record.lastStage,
          reason,
        },
      });
    }
    return open.map((record) => record.id);
  }

  markStage(label, { note = "" } = {}) {
    const open = [...this.#records.values()].filter((record) => OPEN_STATES.has(record.state));
    const liveCount = open.filter((record) => record.state === "live").length;
    const inTransitCount = open.length - liveCount;
    const byOwner = {};
    for (const record of open) {
      byOwner[record.owner] = (byOwner[record.owner] ?? 0) + 1;
    }
    const snapshot = {
      label,
      note,
      at: this.#clock(),
      liveCount,
      inTransitCount,
      openCount: open.length,
      openFrameIds: open.map((record) => record.id),
      byOwner,
    };
    this.#stages.push(snapshot);
    this.#emit(null, "stage", {
      stage: label,
      note,
      liveCount,
      inTransitCount,
      openCount: snapshot.openCount,
      openFrameIds: snapshot.openFrameIds,
      byOwner,
    });
    return snapshot;
  }

  getFrameId(frame) {
    return this.#frameToRecord.get(frame)?.id ?? null;
  }

  getDiagnosticCounts() {
    const counts = {};
    for (const diagnostic of this.#diagnostics) {
      counts[diagnostic.code] = (counts[diagnostic.code] ?? 0) + 1;
    }
    return counts;
  }

  liveness() {
    let reachableWeakTargets = 0;
    for (const record of this.#records.values()) {
      if (record.ref.deref()) {
        reachableWeakTargets += 1;
      }
    }
    return {
      trackedFrames: this.#records.size,
      reachableWeakTargets,
      strongFrameReferencesInTracker: 0,
      frameIndex: "WeakMap<VideoFrame, metadata>",
      recordReference: "WeakRef<VideoFrame>",
    };
  }

  cleanupEvidence() {
    const frames = [...this.#records.values()];
    const openFrames = frames.filter((record) => OPEN_STATES.has(record.state));
    return {
      clean: openFrames.length === 0,
      residualCount: openFrames.length,
      residualFrameIds: openFrames.map((record) => record.id),
      trackedFrameCount: frames.length,
      closedFrameCount: frames.filter((record) => record.state === "closed").length,
      closeEventCount: this.#events.filter((event) => event.type === "close").length,
      allTrackedFramesClosed: frames.every((record) => record.state === "closed"),
      finalStage: this.#stages.at(-1)?.label ?? null,
      finalLiveCount: this.#stages.at(-1)?.openCount ?? openFrames.length,
      storage: this.liveness(),
    };
  }

  exportScenario({ title, description = "", outcome = "completed", metadata = {} } = {}) {
    const frames = [...this.#records.values()]
      .sort((left, right) => left.id.localeCompare(right.id))
      .map((record) => ({
        id: record.id,
        parentId: record.parentId,
        label: record.label,
        state: record.state,
        owner: record.owner,
        lastStage: record.lastStage,
        createdAt: record.createdAt,
        closedAt: record.closedAt,
        closedStage: record.closedStage ?? null,
        closeCount: record.closeCount,
        closeAttempts: record.closeAttempts,
        lastUseStage: record.lastUseStage,
        metadata: { ...record.metadata },
        events: record.events.map((event) => ({ ...event })),
      }));
    return {
      id: this.#scenarioId,
      title: title ?? this.#scenarioId,
      description,
      outcome,
      metadata: { ...metadata },
      summary: {
        frameCount: this.#records.size,
        closedFrameCount: frames.filter((frame) => frame.state === "closed").length,
        openFrameCount: frames.filter((frame) => OPEN_STATES.has(frame.state)).length,
        eventCount: this.#events.length,
        diagnosticCount: this.#diagnostics.length,
        diagnosticCounts: this.getDiagnosticCounts(),
      },
      stages: this.#stages.map((snapshot) => ({
        ...snapshot,
        openFrameIds: [...snapshot.openFrameIds],
        byOwner: { ...snapshot.byOwner },
      })),
      events: this.#events.map((event) => ({ ...event })),
      frames,
      diagnostics: this.#diagnostics.map((diagnostic) => ({
        ...diagnostic,
        details: { ...diagnostic.details },
      })),
      cleanup: this.cleanupEvidence(),
      liveness: this.liveness(),
    };
  }
}
