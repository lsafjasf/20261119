"""DTMF 档案录音检测: 合成 -> 检测 -> 评估 -> HTML 报告。

用法: python3 main.py [--seed N] [--out DIR]
"""
import argparse
import json
import wave
from pathlib import Path

import numpy as np

from dtmf.core import detect
from dtmf.evaluate import evaluate, confusion_matrix, boundary_stats
from dtmf.report import build_report
from dtmf.synth import synthesize


def save_wav(path, signal, fs):
    pcm = (np.clip(signal, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(pcm.tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="out")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(exist_ok=True)

    result = synthesize(seed=args.seed)
    save_wav(out / "recording.wav", result.signal, result.fs)

    events, frames, transitions, noise_floor = detect(result.signal, result.fs)
    ev = evaluate(result.truth, events, result.fs)

    labels, mat = confusion_matrix(ev["confusion"])
    summary = {
        "events": [
            {"digit": e.digit, "start_sample": e.start_sample,
             "end_sample": e.end_sample, "status": e.status,
             "reason": e.reason} for e in events
        ],
        "confusion": {"labels": labels, "matrix": mat},
        "boundary_stats": boundary_stats(ev["boundary"]),
        "reject_check": [
            {"digit": c["truth"].digit, "kind": c["truth"].kind,
             "ok": c["ok"], "note": c["note"]} for c in ev["reject_check"]
        ],
        "misses": [t.digit for t in ev["misses"]],
        "false_alarms": [e.digit for e in ev["false_alarms"]],
    }
    (out / "results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    build_report(result, events, frames, transitions, noise_floor, ev,
                 out / "report.html")

    acc = sum(1 for e in events if e.status == "accepted")
    rej = sum(1 for e in events if e.status == "rejected")
    print(f"接受 {acc} 个, 拒识 {rej} 个, 漏检 {len(ev['misses'])}, "
          f"虚警 {len(ev['false_alarms'])}")
    print(f"拒识核对全部通过: {all(c['ok'] for c in ev['reject_check'])}")
    print(f"边界统计: {summary['boundary_stats']}")
    print(f"输出: {out}/recording.wav, {out}/results.json, {out}/report.html")


if __name__ == "__main__":
    main()
