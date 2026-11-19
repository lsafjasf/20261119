"""Frame-level evaluation against the independent ground-truth JSON.

The estimator never imports this module or the synthesis parameters; this
file only reads the ground-truth file produced separately by ``synth.py``.
"""

from __future__ import annotations

import json

import numpy as np


MATCH_TOL_CENTS = 50.0


def ground_truth_f0_at(gt: dict, t: float) -> list[float]:
    active = []
    for src in gt["sources"]:
        a, b = src["active"]
        if not (a <= t <= b):
            continue
        if src["kind"] == "static":
            active.append(float(src["f0"]))
        else:
            f_lo, f_hi = src["f0"]
            p = (t - a) / (b - a)
            active.append(float(f_lo + (f_hi - f_lo) * p))
    return sorted(active)


def cents(f_est: float, f_ref: float) -> float:
    return 1200.0 * np.log2(f_est / f_ref)


def match_frame(
    est: list[float], ref: list[float], tol_cents: float = MATCH_TOL_CENTS
) -> tuple[list[tuple[int, int, float]], list[int], list[int]]:
    """Greedy nearest matching by cents distance."""
    pairs = []
    for i, e in enumerate(est):
        for j, r in enumerate(ref):
            d = abs(cents(e, r))
            if d <= tol_cents:
                pairs.append((d, i, j))
    pairs.sort()
    used_e, used_r, matches = set(), set(), []
    for d, i, j in pairs:
        if i in used_e or j in used_r:
            continue
        used_e.add(i)
        used_r.add(j)
        matches.append((i, j, d))
    return matches, sorted(used_e), sorted(used_r)


def evaluate(results: list, gt_path: str) -> dict:
    with open(gt_path, encoding="utf-8") as fh:
        gt = json.load(fh)
    total_ref = total_est = matched = 0
    octave_errors = 0
    errors: list[float] = []
    per_segment: dict[str, dict] = {}
    segments = [
        ("octave_missing_f0", 0.1, 2.1),
        ("glide_crossing", 2.3, 4.3),
        ("triple", 4.5, 5.45),
        ("after_stop", 5.6, 6.5),
    ]
    seg_acc = {name: {"ref": 0, "est": 0, "match": 0, "oct": 0, "err": []}
               for name, _, _ in segments}

    for fr in results:
        ref = ground_truth_f0_at(gt, fr.time)
        est = list(fr.f0)
        matches, used_e, _ = match_frame(est, ref)
        total_ref += len(ref)
        total_est += len(est)
        matched += len(matches)
        for i, j, d in matches:
            errors.append(d)
            if 1100.0 <= abs(cents(est[i], ref[j])) <= 1300.0:
                octave_errors += 1
        name = next((n for n, lo, hi in segments if lo <= fr.time <= hi), None)
        if name:
            bag = seg_acc[name]
            bag["ref"] += len(ref)
            bag["est"] += len(est)
            bag["match"] += len(matches)
            bag["err"].extend(d for _, _, d in matches)
            for i, j, d in matches:
                if 1100.0 <= abs(cents(est[i], ref[j])) <= 1300.0:
                    bag["oct"] += 1

    for name, bag in seg_acc.items():
        per_segment[name] = {
            "ground_truth_voices": bag["ref"],
            "estimated_voices": bag["est"],
            "matched": bag["match"],
            "missed": bag["ref"] - bag["match"],
            "false_alarms": bag["est"] - bag["match"],
            "octave_errors": bag["oct"],
            "freq_error_cents_mean": float(np.mean(bag["err"])) if bag["err"] else 0.0,
            "freq_error_cents_p95": float(np.percentile(bag["err"], 95)) if bag["err"] else 0.0,
        }

    return {
        "total_ground_truth": total_ref,
        "total_estimated": total_est,
        "total_matched": matched,
        "recall": matched / max(total_ref, 1),
        "precision": matched / max(total_est, 1),
        "missed": total_ref - matched,
        "false_alarms": total_est - matched,
        "octave_errors": octave_errors,
        "freq_error_cents_mean": float(np.mean(errors)) if errors else 0.0,
        "freq_error_cents_median": float(np.median(errors)) if errors else 0.0,
        "freq_error_cents_p95": float(np.percentile(errors, 95)) if errors else 0.0,
        "per_segment": per_segment,
    }
