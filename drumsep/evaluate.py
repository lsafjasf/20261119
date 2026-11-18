"""Evaluation metrics.

Ground-truth event tables and stems are used ONLY here, never by the
separation pipeline.

Metrics:
  onset error      : detected onsets are picked from the NMF activation
                     matrix H (per-drum rows summed over that drum's
                     templates). Each detection is matched to the
                     nearest true onset of the same drum within a
                     tolerance window; mean absolute error in ms is
                     reported together with miss/false-alarm counts.
  crosstalk energy : the learned soft mask of drum i is applied to the
                     ground-truth isolated stems of the other drums;
                     leaked energy relative to the masked target stem
                     energy, in dB (lower is better).
  residual         : ||V - W H||_F / ||V||_F of the mixture fit.
"""

import numpy as np


def detect_onsets(H, labels, hop, sr, thresh=0.3, min_gap=0.08):
    """Peak-pick per-drum onset times from the activation matrix."""
    from .synth import DRUMS
    onsets = {}
    fps = sr / hop
    for name in DRUMS:
        rows = H[[i for i, lab in enumerate(labels) if lab == name]]
        a = rows.sum(axis=0)
        a = a / (a.max() + 1e-12)
        times = []
        last = -np.inf
        for i in range(1, a.size - 1):
            if a[i] >= thresh and a[i] >= a[i - 1] and a[i] > a[i + 1]:
                t = i / fps
                if t - last >= min_gap:
                    times.append(t)
                    last = t
                elif times and a[i] > a[int(times[-1] * fps)]:
                    times[-1] = t
                    last = t
        onsets[name] = times
    return onsets


def onset_errors(detected, events, tol=0.1):
    """Match detected onsets to ground truth; return per-drum stats."""
    gt = {}
    for t, name in events:
        gt.setdefault(name, []).append(t)
    stats = {}
    for name, truth in gt.items():
        det = sorted(detected.get(name, []))
        used = [False] * len(det)
        errs, missed = [], 0
        for t in sorted(truth):
            best, bi = np.inf, -1
            for j, d in enumerate(det):
                if not used[j] and abs(d - t) < best:
                    best, bi = abs(d - t), j
            if bi >= 0 and best <= tol:
                used[bi] = True
                errs.append(best * 1000.0)
            else:
                missed += 1
        fa = sum(1 for u in used if not u)
        stats[name] = {
            "mean_ms": float(np.mean(errs)) if errs else float("nan"),
            "max_ms": float(np.max(errs)) if errs else float("nan"),
            "matched": len(errs), "missed": missed, "false_alarms": fa,
        }
    return stats


def crosstalk_db(masks, stems_mag, drums):
    """Leakage of other drums through each drum's soft mask, in dB.

    masks: dict drum -> (freq, time) soft mask from separation.
    stems_mag: dict drum -> magnitude STFT of the true isolated stem.
    """
    out = {}
    for name in drums:
        m = masks[name]
        target = np.sum((m * stems_mag[name]) ** 2)
        leak = sum(np.sum((m * stems_mag[o]) ** 2)
                   for o in drums if o != name)
        out[name] = 10 * np.log10((leak + 1e-12) / (target + 1e-12))
    return out


def residual(V, W, H):
    return float(np.linalg.norm(V - W @ H) / (np.linalg.norm(V) + 1e-12))


def waveform_snr_db(sep, stems, drums):
    """Per-drum SNR of the separated waveform vs the true stem."""
    out = {}
    for name in drums:
        s = stems[name]
        err = sep[name] - s
        out[name] = 10 * np.log10(
            (np.sum(s ** 2) + 1e-12) / (np.sum(err ** 2) + 1e-12))
    return out
