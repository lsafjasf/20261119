(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.CutTraceAudit = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const STATUS = Object.freeze({
    AVAILABLE: "available",
    CONFLICT: "conflict",
    MISSING: "missing",
    AMBIGUOUS: "ambiguous",
  });

  const STATUS_META = Object.freeze({
    available: {
      label: "Available",
      shortLabel: "Verified",
      description: "Proxy metadata and source fingerprint match the original.",
    },
    conflict: {
      label: "Conflict",
      shortLabel: "Conflict",
      description: "A proxy is linked, but one or more metadata fields differ.",
    },
    missing: {
      label: "Missing",
      shortLabel: "Missing",
      description: "No proxy exists. Playback and output are blocked.",
    },
    ambiguous: {
      label: "Ambiguous",
      shortLabel: "Needs match",
      description: "One or more same-name candidates exist. A user decision is required.",
    },
  });

  const FIELD_DEFS = Object.freeze([
    { key: "reel", label: "Reel", kind: "text" },
    { key: "clip", label: "Clip", kind: "text" },
    { key: "startFrame", label: "Start frame", kind: "number" },
    { key: "frameRate", label: "Frame rate", kind: "fps" },
    { key: "durationFrames", label: "Duration", kind: "duration" },
  ]);

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function formatFrameRate(value) {
    if (value === null || value === undefined) return "—";
    return `${Number(value).toFixed(3)} fps`;
  }

  function formatDuration(frames, frameRate) {
    if (frames === null || frames === undefined) return "—";
    const safeRate = Number(frameRate) || 24;
    const totalSeconds = Number(frames) / safeRate;
    const wholeSeconds = Math.floor(totalSeconds + 0.000001);
    const hours = Math.floor(wholeSeconds / 3600);
    const minutes = Math.floor((wholeSeconds % 3600) / 60);
    const seconds = wholeSeconds % 60;
    let framePart = Math.round((totalSeconds - wholeSeconds) * safeRate);
    if (framePart >= Math.round(safeRate)) framePart = 0;
    return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}:${pad(framePart)} · ${frames}f`;
  }

  function formatTimecode(frames, frameRate) {
    const display = formatDuration(frames, frameRate);
    return display.split(" · ")[0];
  }

  function formatFieldValue(field, asset, source) {
    if (!asset) return "—";
    if (field.kind === "duration") {
      return formatDuration(source.durationFrames, source.frameRate);
    }
    if (field.kind === "fps") return formatFrameRate(source.frameRate);
    return String(source[field.key]);
  }

  function valuesMatch(field, original, proxy) {
    if (!original || !proxy) return false;
    if (field.kind === "number" || field.kind === "duration") {
      return Number(original[field.key]) === Number(proxy[field.key]);
    }
    if (field.kind === "fps") {
      return Number(original.frameRate) === Number(proxy.frameRate);
    }
    return String(original[field.key]) === String(proxy[field.key]);
  }

  function numericDelta(originalValue, proxyValue, unit) {
    const delta = Number(proxyValue) - Number(originalValue);
    if (!Number.isFinite(delta) || delta === 0) return "0 " + unit;
    return `${delta > 0 ? "+" : ""}${delta} ${unit}`;
  }

  function describeDelta(field, original, proxy) {
    if (field.kind === "number") {
      return numericDelta(original[field.key], proxy[field.key], "frames");
    }
    if (field.kind === "duration") {
      return numericDelta(original.durationFrames, proxy.durationFrames, "frames");
    }
    if (field.kind === "fps") {
      return `${Number(proxy.frameRate).toFixed(3)} vs ${Number(original.frameRate).toFixed(3)}`;
    }
    return `${proxy[field.key]} vs ${original[field.key]}`;
  }

  function findById(items, id) {
    return items.find((item) => item.id === id) || null;
  }

  function findCandidates(clip, proxies) {
    if (Array.isArray(clip.candidateIds)) {
      return clip.candidateIds.map((id) => findById(proxies, id)).filter(Boolean);
    }
    const reference = clip.timelineReference || "";
    return proxies.filter((proxy) => proxy.filename === reference);
  }

  function buildFields(original, proxy) {
    return FIELD_DEFS.map((field) => {
      const hasProxy = Boolean(proxy);
      const same = hasProxy ? valuesMatch(field, original, proxy) : false;
      return {
        key: field.key,
        label: field.label,
        kind: field.kind,
        originalValue: formatFieldValue(field, original, original),
        proxyValue: hasProxy ? formatFieldValue(field, proxy, proxy) : "—",
        verdict: !hasProxy ? "missing" : same ? "match" : "diff",
        equal: same,
      };
    });
  }

  function buildFieldDifferences(original, proxy, fields) {
    return fields
      .filter((field) => field.verdict === "diff")
      .map((field) => {
        const definition = FIELD_DEFS.find((item) => item.key === field.key);
        return {
          key: field.key,
          fieldKey: field.key,
          label: field.label,
          type: "conflict",
          originalValue: field.originalValue,
          proxyValue: field.proxyValue,
          delta: describeDelta(definition, original, proxy),
          message: `${field.label} differs between the source and proxy.`,
        };
      });
  }

  function scanClip(clip, data) {
    const original = findById(data.originals, clip.originalId);
    const proxy = clip.proxyId ? findById(data.proxies, clip.proxyId) : null;
    const candidates = findCandidates(clip, data.proxies);
    const fields = buildFields(original, proxy);
    const signatureMatch = Boolean(
      proxy && original && proxy.signature === original.signature
    );

    let status;
    if (!proxy && candidates.length > 0) {
      status = STATUS.AMBIGUOUS;
    } else if (!proxy) {
      status = STATUS.MISSING;
    } else {
      const metadataMatch = fields.every((field) => field.equal);
      status = metadataMatch && signatureMatch ? STATUS.AVAILABLE : STATUS.CONFLICT;
    }

    let differences = [];
    if (status === STATUS.AMBIGUOUS) {
      differences = [
        {
          key: "identity",
          fieldKey: "identity",
          label: "Source identity",
          type: "ambiguous",
          originalValue: clip.timelineReference || original.filename,
          proxyValue: `${candidates.length} same-name candidate${candidates.length === 1 ? "" : "s"}`,
          delta: "Decision required",
          message:
            "Filename matches more than one asset. No source association was created.",
        },
      ];
    } else if (status === STATUS.MISSING) {
      differences = [
        {
          key: "proxy",
          fieldKey: "proxy",
          label: "Proxy reference",
          type: "missing",
          originalValue: clip.timelineReference || original.filename,
          proxyValue: "Missing",
          delta: "No candidate",
          message:
            "No proxy is available. The editor must block this clip instead of substituting silence or an empty frame.",
        },
      ];
    } else if (proxy) {
      differences = buildFieldDifferences(original, proxy, fields);
      if (!signatureMatch) {
        differences.push({
          key: "signature",
          fieldKey: "signature",
          label: "Content fingerprint",
          type: "conflict",
          originalValue: original.signature,
          proxyValue: proxy.signature,
          delta: "Mismatch",
          message:
            "The proxy content signature does not match the original source. Relink before output.",
        });
      }
    }

    const timelineReference =
      clip.timelineReference || proxy?.filename || original?.filename || "Unknown clip";
    const mappingState =
      status === STATUS.AVAILABLE
        ? "verified"
        : status === STATUS.CONFLICT
          ? "drift"
          : status === STATUS.AMBIGUOUS
            ? "withheld"
            : "missing";

    return {
      clipId: clip.id,
      order: clip.order,
      clip,
      original,
      proxy,
      candidates,
      status,
      statusMeta: STATUS_META[status],
      fields,
      differences,
      diffCount: differences.length,
      signatureMatch,
      traceable: Boolean(proxy && signatureMatch),
      playbackBlocked: status === STATUS.MISSING || status === STATUS.AMBIGUOUS,
      needsReview: status !== STATUS.AVAILABLE,
      resolvedByUser: Boolean(clip.resolution?.proxyId),
      timelineReference,
      timelineStartFrame: clip.timelineStartFrame,
      timelineTimecode: formatTimecode(clip.timelineStartFrame, data.sequence.frameRate),
      art: clip.art,
      mapping: {
        timelineReference,
        proxyId: proxy?.id || null,
        proxyFilename: proxy?.filename || null,
        originalId: original?.id || null,
        originalFilename: original?.filename || null,
        method: clip.resolution
          ? "Editor confirmed"
          : status === STATUS.AMBIGUOUS
            ? "Withheld · filename collision"
            : status === STATUS.MISSING
              ? "No proxy candidate"
              : clip.method,
        state: mappingState,
      },
    };
  }

  function buildAudit(data) {
    const clips = data.clips.map((clip) => scanClip(clip, data));
    const summary = {
      total: clips.length,
      available: clips.filter((clip) => clip.status === STATUS.AVAILABLE).length,
      conflict: clips.filter((clip) => clip.status === STATUS.CONFLICT).length,
      missing: clips.filter((clip) => clip.status === STATUS.MISSING).length,
      ambiguous: clips.filter((clip) => clip.status === STATUS.AMBIGUOUS).length,
      blocked: clips.filter((clip) => clip.playbackBlocked).length,
      differences: clips.reduce((count, clip) => count + clip.diffCount, 0),
    };
    return {
      sequence: data.sequence,
      clips,
      summary,
      gate: {
        blocked: summary.blocked > 0,
        reason:
          summary.missing > 0
            ? `${summary.missing} missing proxy · playback cannot continue`
            : summary.ambiguous > 0
              ? `${summary.ambiguous} unresolved source match`
              : summary.conflict > 0
                ? `${summary.conflict} metadata conflicts require review`
                : "All timeline references are traceable",
      },
    };
  }

  function resolveClipCandidate(data, clipId, proxyId) {
    const clip = findById(data.clips, clipId);
    if (!clip) throw new Error(`Unknown clip: ${clipId}`);
    if (!Array.isArray(clip.candidateIds) || !clip.candidateIds.includes(proxyId)) {
      throw new Error(`Proxy ${proxyId} is not a candidate for ${clipId}`);
    }
    return {
      ...data,
      clips: data.clips.map((item) =>
        item.id === clipId
          ? {
              ...item,
              proxyId,
              resolution: {
                proxyId,
                by: "User",
                resolvedAt: new Date().toISOString(),
              },
            }
          : item
      ),
    };
  }

  function serializeAsset(asset) {
    if (!asset) return null;
    return {
      id: asset.id,
      filename: asset.filename,
      path: asset.path,
      reel: asset.reel,
      clip: asset.clip,
      startFrame: asset.startFrame,
      frameRate: asset.frameRate,
      durationFrames: asset.durationFrames,
      signature: asset.signature,
      resolution: asset.resolution,
      codec: asset.codec,
      size: asset.size,
    };
  }

  function buildReport(data, options) {
    const audit = buildAudit(data);
    const generatedAt = options?.generatedAt || new Date().toISOString();
    return {
      generatedAt,
      sequence: {
        id: data.sequence.id,
        version: data.sequence.version,
        title: data.sequence.title,
        durationFrames: data.sequence.durationFrames,
      },
      summary: audit.summary,
      traceGate: audit.gate,
      mappingTable: audit.clips.map((entry) => ({
        clipId: entry.clipId,
        timelineTimecode: entry.timelineTimecode,
        timelineReference: entry.timelineReference,
        proxyId: entry.mapping.proxyId,
        originalId: entry.mapping.originalId,
        method: entry.mapping.method,
        state: entry.mapping.state,
        status: entry.status,
        traceable: entry.traceable,
        playbackBlocked: entry.playbackBlocked,
      })),
      checklist: audit.clips.map((entry) => ({
        clipId: entry.clipId,
        filename: entry.timelineReference,
        status: entry.status,
        playbackBlocked: entry.playbackBlocked,
        original: serializeAsset(entry.original),
        proxy: serializeAsset(entry.proxy),
        candidates: entry.candidates.map(serializeAsset),
        differences: entry.differences,
      })),
      findings: audit.clips.flatMap((entry) =>
        entry.differences.map((difference) => ({
          clipId: entry.clipId,
          filename: entry.timelineReference,
          status: entry.status,
          ...difference,
        }))
      ),
    };
  }

  return {
    STATUS,
    STATUS_META,
    FIELD_DEFS,
    formatFrameRate,
    formatDuration,
    formatTimecode,
    scanClip,
    buildAudit,
    resolveClipCandidate,
    buildReport,
  };
});
