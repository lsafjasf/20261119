"""命令行入口。

  python -m deflicker.cli synth --out data/synth
  python -m deflicker.cli run data/synth/flicker.mp4 --out out/run1
  python -m deflicker.cli run data/synth/flicker.mp4 --out out/run1 \
      --exclude 40,150,90,80 --recompute 0 60
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from . import core, metrics, synth


def _parse_rect(text: str) -> tuple[int, int, int, int]:
    parts = [int(v) for v in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("矩形格式为 x,y,w,h")
    return tuple(parts)  # type: ignore[return-value]


def _load_or_estimate_gain(
    video: str,
    cache: Path,
    exclude_rects: list[tuple[int, int, int, int]],
    recompute: tuple[int, int] | None,
) -> tuple[np.ndarray, float, np.ndarray, np.ndarray]:
    """估计原始对数增益；带缓存时仅重算 --recompute 指定的受影响区间。"""
    log_frames, fps = core.read_video_gray(video)
    t, h, w = log_frames.shape
    exclude = core.rects_to_mask(h, w, exclude_rects) if exclude_rects else None
    stable = core.estimate_stable_mask(log_frames, exclude)

    if cache.exists() and recompute is not None:
        s_raw = np.load(cache)["s_raw"].copy()
        t0, t1 = recompute
        t1 = min(t1, t)
        s_raw[t0:t1] = core.estimate_raw_gain(log_frames[t0:t1], stable)
        print(f"[run] 重算区间 [{t0}, {t1})，其余帧沿用缓存增益")
    else:
        s_raw = core.estimate_raw_gain(log_frames, stable)
    # 归一化到初始电平，便于与真值阶跃对比（增益参考点本来任意）
    s_raw = s_raw - np.median(s_raw[:10])
    np.savez_compressed(cache, s_raw=s_raw, stable=stable)
    return s_raw, fps, log_frames, stable


def cmd_synth(args: argparse.Namespace) -> None:
    meta = synth.synthesize(
        args.out, n=args.frames, flicker_hz=args.flicker_hz,
        flicker_amp=args.flicker_amp, step_frame=args.step_frame,
        step_gain=args.step_gain,
    )
    print(f"[synth] 素材已生成到 {args.out}: {json.dumps(meta)}")


def _plot_gain_curves(out: Path, dec: core.Decomposition, fps: float,
                      gt: dict | None, dec_after: core.Decomposition) -> None:
    t = np.arange(len(dec.s_raw)) / fps
    fig, axes = plt.subplots(3, 1, figsize=(9, 9), sharex=True)

    axes[0].plot(t, np.exp(dec.s_raw), label="estimated raw gain", color="C0")
    axes[0].plot(t, np.exp(dec.trend), label="trend (kept)", color="C2", lw=2)
    if gt is not None:
        axes[0].plot(t, gt["g_flicker"] * gt["g_step"], "--",
                     label="ground truth total", color="k", alpha=0.6)
    for s in dec.steps:
        axes[0].axvline(s / fps, color="r", ls=":")
    axes[0].set_ylabel("gain")
    axes[0].legend()
    axes[0].set_title("Estimated gain curve (step preserved)")

    axes[1].plot(t, np.exp(dec.flicker), label="estimated flicker", color="C1")
    if gt is not None:
        axes[1].plot(t, gt["g_flicker"], "--", label="ground truth flicker",
                     color="k", alpha=0.6)
    axes[1].set_ylabel("flicker gain")
    axes[1].legend()
    axes[1].set_title("Periodic component (removed)")

    axes[2].plot(t, np.exp(dec_after.s_raw), label="residual gain after correction",
                 color="C0")
    axes[2].plot(t, np.exp(dec_after.trend), label="residual trend", color="C2", lw=2)
    if gt is not None:
        axes[2].plot(t, gt["g_step"], "--", label="ground truth step",
                     color="k", alpha=0.6)
    axes[2].set_ylabel("gain")
    axes[2].set_xlabel("time (s)")
    axes[2].legend()
    axes[2].set_title("Residual gain after correction (should keep the step)")

    fig.tight_layout()
    fig.savefig(out / "gain_curve.png", dpi=110)
    plt.close(fig)


def cmd_run(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "gain_cache.npz"
    recompute = tuple(args.recompute) if args.recompute else None

    s_raw, fps, log_frames, stable = _load_or_estimate_gain(
        args.video, cache, args.exclude or [], recompute
    )
    flicker_hz = args.flicker_hz
    if flicker_hz <= 0:
        flicker_hz = metrics.dominant_frequency(s_raw - core.median_filter(s_raw, 31), fps)
        print(f"[run] 自动检测频闪主频: {flicker_hz:.2f} Hz")
    dec = core.decompose_gain(s_raw, fps, flicker_hz)
    print(f"[run] 检测到真实照明阶跃位置: {dec.steps}")

    frames, _ = core.read_video_color(args.video)
    corrected = core.compensate(frames, dec.flicker)
    core.write_video(str(out / "corrected.mp4"), corrected, fps)

    # 补偿后重新估计残余增益，验证阶跃保留、周期消除
    log_corr = np.log(corrected.mean(axis=3) + core.EPS)
    s_after = core.estimate_raw_gain(log_corr, stable)
    dec_after = core.decompose_gain(s_after, fps, flicker_hz)

    report = metrics.residual_periodic_ratio(dec.flicker, dec_after.flicker, fps,
                                             f0=flicker_hz)
    report["steps_detected"] = dec.steps
    report["stable_pixel_ratio"] = float(stable.mean())

    gt_path = Path(args.video).with_name("gt.npz")
    gt = None
    if gt_path.exists():
        gt_data = np.load(gt_path)
        gt = {k: gt_data[k] for k in ("g_flicker", "g_step")}
        ideal, _ = core.read_video_color(str(gt_path.with_name("ideal.mp4")))
        errs, fg_stats = metrics.foreground_brightness_error(
            corrected, ideal, gt_data["fg_alpha"]
        )
        report.update(fg_stats)
        fig, ax = plt.subplots(figsize=(9, 3))
        ax.plot(errs, color="C3")
        ax.set_xlabel("frame")
        ax.set_ylabel("mean |err|")
        ax.set_title("Foreground brightness error")
        fig.tight_layout()
        fig.savefig(out / "fg_error.png", dpi=110)
        plt.close(fig)

    _plot_gain_curves(out, dec, fps, gt, dec_after)
    (out / "metrics.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False)
    )
    print(f"[run] 指标: {json.dumps(report, indent=2, ensure_ascii=False)}")
    print(f"[run] 输出: {out}/corrected.mp4, gain_curve.png, metrics.json")


def main() -> None:
    parser = argparse.ArgumentParser(prog="deflicker", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_syn = sub.add_parser("synth", help="生成合成测试素材")
    p_syn.add_argument("--out", required=True)
    p_syn.add_argument("--frames", type=int, default=120)
    p_syn.add_argument("--flicker-hz", type=float, default=4.0)
    p_syn.add_argument("--flicker-amp", type=float, default=0.18)
    p_syn.add_argument("--step-frame", type=int, default=70)
    p_syn.add_argument("--step-gain", type=float, default=1.25)
    p_syn.set_defaults(func=cmd_synth)

    p_run = sub.add_parser("run", help="估计增益并补偿")
    p_run.add_argument("video")
    p_run.add_argument("--out", required=True)
    p_run.add_argument("--flicker-hz", type=float, default=-1.0,
                       help="频闪主频；<=0 时自动检测")
    p_run.add_argument("--exclude", type=_parse_rect, action="append",
                       help="人工排除区域 x,y,w,h，可重复")
    p_run.add_argument("--recompute", type=int, nargs=2, metavar=("T0", "T1"),
                       help="仅重算帧区间 [T0, T1) 的增益（配合缓存）")
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
