"""评估: 将检测事件与真值对齐, 输出混淆表、边界误差与拒识核对。"""
from collections import defaultdict

from .core import KEYPAD

DIGITS = list("1234567890*#") + ["A", "B", "C", "D"]
NONE = "-"


def _overlap(a0, a1, b0, b1):
    return max(0, min(a1, b1) - max(a0, b0))


def evaluate(truth, events, fs):
    """返回 dict: confusion, boundary_errors, reject_check, misses, false_alarms。"""
    accepted = [e for e in events if e.status == "accepted"]
    rejected = [e for e in events if e.status == "rejected"]

    confusion = defaultdict(lambda: defaultdict(int))
    boundary = []           # (digit, start_err_ms, end_err_ms)
    matched_events = set()
    misses = []
    reject_check = []       # (truth_item, 是否被正确拒识, 说明)

    valid_truth = [t for t in truth if t.kind == "valid"]
    bad_truth = [t for t in truth if t.kind != "valid"]

    for t in valid_truth:
        best, best_ov = None, 0
        for i, e in enumerate(accepted):
            if i in matched_events:
                continue
            ov = _overlap(t.start_sample, t.end_sample, e.start_sample, e.end_sample)
            if ov > best_ov:
                best, best_ov = (i, e), ov
        dur = t.end_sample - t.start_sample
        if best is not None and best_ov > 0.4 * dur:
            i, e = best
            matched_events.add(i)
            confusion[t.digit][e.digit] += 1
            boundary.append({
                "digit": t.digit,
                "start_err_ms": (e.start_sample - t.start_sample) / fs * 1000.0,
                "end_err_ms": (e.end_sample - t.end_sample) / fs * 1000.0,
            })
        else:
            confusion[t.digit][NONE] += 1
            misses.append(t)

    false_alarms = []
    for i, e in enumerate(accepted):
        if i in matched_events:
            continue
        # 与任何应拒识真值重叠的接受事件也算虚警
        confusion[NONE][e.digit] += 1
        false_alarms.append(e)

    # 拒识核对: 过短/重叠真值不应被任何 accepted 事件覆盖
    for t in bad_truth:
        hit = None
        for e in accepted:
            if _overlap(t.start_sample, t.end_sample,
                        e.start_sample, e.end_sample) > 0:
                hit = e
                break
        ok = hit is None
        note = "正确拒识" if ok else f"误接受为 {hit.digit}"
        reject_check.append({"truth": t, "ok": ok, "note": note})

    return {
        "confusion": confusion,
        "boundary": boundary,
        "misses": misses,
        "false_alarms": false_alarms,
        "reject_check": reject_check,
        "accepted": accepted,
        "rejected": rejected,
    }


def confusion_matrix(confusion):
    """转成 (labels, matrix) 便于制表。"""
    labels = DIGITS + [NONE]
    mat = [[confusion.get(r, {}).get(c, 0) for c in labels] for r in labels]
    return labels, mat


def boundary_stats(boundary):
    if not boundary:
        return {}
    import numpy as np
    s = np.array([b["start_err_ms"] for b in boundary])
    e = np.array([b["end_err_ms"] for b in boundary])
    return {
        "n": len(boundary),
        "start_mean_ms": float(s.mean()), "start_std_ms": float(s.std()),
        "start_maxabs_ms": float(abs(s).max()),
        "end_mean_ms": float(e.mean()), "end_std_ms": float(e.std()),
        "end_maxabs_ms": float(abs(e).max()),
    }
