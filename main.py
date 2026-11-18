"""VAD 命令行：生成测试集 / 检测 / 评估（阈值可覆盖并重新计算区间级指标）。

用法:
  python main.py testset --out data
  python main.py detect  --wav data/testset.wav [--set key=value ...]
  python main.py eval    --wav data/testset.wav --ann data/testset.ann.json [--set key=value ...]
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
from scipy.io import wavfile

from vad.detector import VadParams, detect_waveform, Segment
from vad.evaluate import evaluate, format_report
from vad import testset


def parse_overrides(items: list[str]) -> dict:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"--set 参数格式应为 key=value，收到: {it}")
        k, v = it.split("=", 1)
        out[k.strip()] = float(v)
    return out


def build_params(overrides: dict) -> VadParams:
    p = VadParams()
    valid = set(p.to_dict())
    for k, v in overrides.items():
        if k not in valid:
            raise SystemExit(f"未知参数 {k}，可选: {sorted(valid)}")
        setattr(p, k, v)
    if p.exit_offset_db >= p.enter_offset_db:
        print("警告: exit_offset_db 应小于 enter_offset_db 以形成滞回",
              file=sys.stderr)
    return p


def show_state_machine(p: VadParams, noise_db: float | None = None,
                       enter_db: float | None = None,
                       exit_db: float | None = None) -> None:
    print("========== 状态机配置 ==========")
    print(f"分帧: {p.frame_ms:.0f}ms / 步长 {p.hop_ms:.0f}ms")
    print(f"滞回阈值(相对噪声底): 进入 +{p.enter_offset_db:.1f}dB  "
          f"退出 +{p.exit_offset_db:.1f}dB")
    if noise_db is not None:
        print(f"噪声底估计: {noise_db:.1f}dB  进入阈值 {enter_db:.1f}dB  "
              f"退出阈值 {exit_db:.1f}dB")
    print(f"键盘门控: ZCR>{p.zcr_hi:.2f} 且 平坦度>{p.flat_hi:.2f} 视为敲击样帧")
    print(f"最短触发时长 min_onset: {p.min_onset_ms:.0f}ms  "
          f"(短于此的瞬态不能触发开始)")
    print(f"挂起 hangover: {p.hangover_ms:.0f}ms  "
          f"合并间隔 merge_gap: {p.merge_gap_ms:.0f}ms  "
          f"最短语音段 min_speech: {p.min_speech_ms:.0f}ms")


def load_wav(path: str) -> tuple[np.ndarray, int]:
    sr, data = wavfile.read(path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if np.issubdtype(data.dtype, np.integer):
        data = data.astype(np.float32) / np.iinfo(data.dtype).max
    return data.astype(np.float32), sr


def cmd_testset(args) -> None:
    wav_path, ann_path = testset.generate(args.out)
    print(f"测试集已生成:\n  音频: {wav_path}\n  标注: {ann_path}")


def cmd_detect(args) -> None:
    params = build_params(parse_overrides(args.set))
    x, sr = load_wav(args.wav)
    res = detect_waveform(x, sr, params)
    show_state_machine(params, res.noise_db, res.enter_db, res.exit_db)
    print("========== 检出语音段 ==========")
    if not res.segments:
        print("  (无)")
    for i, s in enumerate(res.segments):
        print(f"  #{i + 1}  [{s.start:7.2f}s - {s.end:7.2f}s]  时长 {s.dur:.2f}s")


def cmd_eval(args) -> None:
    params = build_params(parse_overrides(args.set))
    x, sr = load_wav(args.wav)
    with open(args.ann, encoding="utf-8") as f:
        ann = json.load(f)
    gt = [Segment(iv["start"], iv["end"]) for iv in ann["intervals"]
          if iv["label"] == "speech"]
    total = float(ann.get("duration", len(x) / sr))
    res = detect_waveform(x, sr, params)
    show_state_machine(params, res.noise_db, res.enter_db, res.exit_db)
    print("========== 检出语音段 ==========")
    for i, s in enumerate(res.segments):
        print(f"  #{i + 1}  [{s.start:7.2f}s - {s.end:7.2f}s]  时长 {s.dur:.2f}s")
    rep = evaluate(res.segments, gt, total)
    print(format_report(rep))


def main() -> None:
    ap = argparse.ArgumentParser(
        description="长录音 VAD：能量+ZCR+频谱平坦度，滞回状态机")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ts = sub.add_parser("testset", help="生成合成测试集(语音/键盘/空调, 逐段标注)")
    p_ts.add_argument("--out", default="data")
    p_ts.set_defaults(fn=cmd_testset)

    p_dt = sub.add_parser("detect", help="仅检测并输出语音段")
    p_dt.add_argument("--wav", required=True)
    p_dt.add_argument("--set", action="append", default=[],
                      help="覆盖阈值, 如 --set enter_offset_db=12")
    p_dt.set_defaults(fn=cmd_detect)

    p_ev = sub.add_parser("eval", help="检测并用标注评估漏检/误检/边界误差")
    p_ev.add_argument("--wav", required=True)
    p_ev.add_argument("--ann", required=True)
    p_ev.add_argument("--set", action="append", default=[],
                      help="覆盖阈值后重新计算区间级指标")
    p_ev.set_defaults(fn=cmd_eval)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
