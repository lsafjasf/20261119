"""区间级评估：漏检、误检、边界误差（标注仅用于评价，不参与检测）。"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .detector import Segment


@dataclass
class MatchedPair:
    gt: Segment
    det: Segment
    start_err_ms: float   # det.start - gt.start（正=检出偏晚）
    end_err_ms: float     # det.end - gt.end


@dataclass
class EvalReport:
    n_gt: int
    n_det: int
    matched: list[MatchedPair] = field(default_factory=list)
    missed: list[Segment] = field(default_factory=list)        # 漏检的标注段
    false_alarms: list[Segment] = field(default_factory=list)  # 误检的检出段
    gt_speech_s: float = 0.0
    det_speech_s: float = 0.0
    miss_s: float = 0.0      # 标注语音未被覆盖的时长
    false_s: float = 0.0     # 检出落在非语音上的时长
    miss_rate: float = 0.0
    false_rate: float = 0.0
    mean_start_err_ms: float = 0.0
    mean_end_err_ms: float = 0.0
    max_start_err_ms: float = 0.0
    max_end_err_ms: float = 0.0


def _overlap(a: Segment, b: Segment) -> float:
    return max(0.0, min(a.end, b.end) - max(a.start, b.start))


def evaluate(detected: list[Segment], ground_truth: list[Segment],
             total_dur: float, match_iou_min: float = 0.1) -> EvalReport:
    """贪心一对一匹配：按 IoU 最大优先配对。"""
    rep = EvalReport(n_gt=len(ground_truth), n_det=len(detected))
    rep.gt_speech_s = sum(s.dur for s in ground_truth)
    rep.det_speech_s = sum(s.dur for s in detected)

    pairs = []
    for gi, g in enumerate(ground_truth):
        for di, d in enumerate(detected):
            ov = _overlap(g, d)
            if ov > 0:
                union = g.dur + d.dur - ov
                pairs.append((ov / union, ov, gi, di))
    pairs.sort(key=lambda p: (-p[0], -p[1]))

    used_g, used_d = set(), set()
    for iou, _ov, gi, di in pairs:
        if iou < match_iou_min or gi in used_g or di in used_d:
            continue
        used_g.add(gi)
        used_d.add(di)
        g, d = ground_truth[gi], detected[di]
        rep.matched.append(MatchedPair(
            gt=g, det=d,
            start_err_ms=(d.start - g.start) * 1000.0,
            end_err_ms=(d.end - g.end) * 1000.0,
        ))

    rep.missed = [g for gi, g in enumerate(ground_truth) if gi not in used_g]
    rep.false_alarms = [d for di, d in enumerate(detected) if di not in used_d]

    if total_dur > 0:
        dt = 0.01
        t = np.arange(0, total_dur, dt)
        gt_mask = _mask(t, ground_truth)
        det_mask = _mask(t, detected)
        rep.miss_s = float(np.sum(gt_mask & ~det_mask) * dt)
        rep.false_s = float(np.sum(det_mask & ~gt_mask) * dt)
        nonspeech = max(1e-9, total_dur - rep.gt_speech_s)
        rep.miss_rate = rep.miss_s / max(1e-9, rep.gt_speech_s)
        rep.false_rate = rep.false_s / nonspeech

    if rep.matched:
        se = np.array([m.start_err_ms for m in rep.matched])
        ee = np.array([m.end_err_ms for m in rep.matched])
        rep.mean_start_err_ms = float(np.mean(np.abs(se)))
        rep.mean_end_err_ms = float(np.mean(np.abs(ee)))
        rep.max_start_err_ms = float(np.max(np.abs(se)))
        rep.max_end_err_ms = float(np.max(np.abs(ee)))
    return rep


def _mask(t: np.ndarray, segs: list[Segment]) -> np.ndarray:
    m = np.zeros(len(t), dtype=bool)
    for s in segs:
        m |= (t >= s.start) & (t < s.end)
    return m


def format_report(rep: EvalReport) -> str:
    lines = [
        "========== 区间级评估结果 ==========",
        f"标注语音段: {rep.n_gt}  检出段: {rep.n_det}  匹配: {len(rep.matched)}",
        f"漏检段数: {len(rep.missed)}  误检段数: {len(rep.false_alarms)}",
        f"漏检时长: {rep.miss_s:.2f}s / {rep.gt_speech_s:.2f}s "
        f"(漏检率 {rep.miss_rate * 100:.1f}%)",
        f"误检时长: {rep.false_s:.2f}s (误检率 {rep.false_rate * 100:.1f}%)",
    ]
    if rep.matched:
        lines += [
            f"边界误差 |起点|: 均值 {rep.mean_start_err_ms:.0f}ms  "
            f"最大 {rep.max_start_err_ms:.0f}ms",
            f"边界误差 |终点|: 均值 {rep.mean_end_err_ms:.0f}ms  "
            f"最大 {rep.max_end_err_ms:.0f}ms",
        ]
    if rep.missed:
        lines.append("--- 漏检段 (标注) ---")
        for s in rep.missed:
            lines.append(f"  [{s.start:7.2f}s - {s.end:7.2f}s] 时长 {s.dur:.2f}s")
    if rep.false_alarms:
        lines.append("--- 误检段 (检出) ---")
        for s in rep.false_alarms:
            lines.append(f"  [{s.start:7.2f}s - {s.end:7.2f}s] 时长 {s.dur:.2f}s")
    if rep.matched:
        lines.append("--- 匹配段边界误差 ---")
        for m in rep.matched:
            lines.append(
                f"  GT[{m.gt.start:7.2f},{m.gt.end:7.2f}] "
                f"DET[{m.det.start:7.2f},{m.det.end:7.2f}] "
                f"起点 {m.start_err_ms:+6.0f}ms 终点 {m.end_err_ms:+6.0f}ms")
    return "\n".join(lines)
