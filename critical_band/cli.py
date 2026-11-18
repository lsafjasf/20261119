"""命令行入口。

示例
----
python -m critical_band --kind tone --tone-freq 1000 --plot out.png
python -m critical_band --kind two_tone --f1 980 --f2 1020
python -m critical_band --kind noise --probe-freq 2000 --probe-amp -35
python -m critical_band --check-energy --n-fft 4096
python -m critical_band --interactive
"""

from __future__ import annotations

import argparse
import sys

import numpy as np

from .config import ModelParams
from .signals import make_signal
from .analysis import run_analysis, band_energy_invariance
from .bark import BARK_BANDS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="critical_band",
        description="Bark 临界带分析与音调/噪声掩蔽阈值演示工具 "
                    "（非医学听力评估）")
    p.add_argument("--kind", choices=["tone", "two_tone", "noise"],
                   default="tone", help="输入信号类型")
    p.add_argument("--duration", type=float, default=1.0, help="信号时长 s")
    p.add_argument("--fs", type=float, default=48000.0, help="采样率 Hz")
    p.add_argument("--n-fft", type=int, default=4096, help="FFT 长度")
    p.add_argument("--win-length", type=int, default=2048, help="窗长度")
    p.add_argument("--hop-ratio", type=float, default=0.5, help="帧移比例")
    p.add_argument("--window", default="hann",
                   choices=["hann", "hamming", "blackman", "rect"])
    p.add_argument("--full-scale-spl", type=float, default=94.0,
                   help="0 dBFS 对应的 dB SPL 标定")
    p.add_argument("--tonal-offset", type=float, default=-6.0)
    p.add_argument("--noise-offset", type=float, default=-2.3)
    p.add_argument("--spread-down", type=float, default=27.0,
                   help="低频方向扩散斜率 dB/Bark")
    p.add_argument("--spread-up", type=float, default=12.0,
                   help="高频方向扩散斜率 dB/Bark")
    p.add_argument("--tone-freq", type=float, default=1000.0)
    p.add_argument("--tone-amp", type=float, default=-20.0)
    p.add_argument("--f1", type=float, default=980.0)
    p.add_argument("--f2", type=float, default=1020.0)
    p.add_argument("--amp1", type=float, default=-20.0)
    p.add_argument("--amp2", type=float, default=-26.0)
    p.add_argument("--noise-amp", type=float, default=-20.0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--probe-freq", type=float, default=None,
                   help="探测音频率 Hz（不指定则不加探测音）")
    p.add_argument("--probe-amp", type=float, default=-30.0,
                   help="探测音幅度 dBFS")
    p.add_argument("--plot", default=None, help="保存分析图到该路径 (.png)")
    p.add_argument("--check-energy", action="store_true",
                   help="改变 FFT 长度检查带内能量一致性")
    p.add_argument("--show-params", action="store_true",
                   help="打印全部模型参数")
    p.add_argument("--interactive", action="store_true",
                   help="启动交互式滑块窗口")
    return p


def _params_from_args(a) -> ModelParams:
    return ModelParams(
        fs=a.fs, n_fft=a.n_fft, win_length=a.win_length,
        hop_ratio=a.hop_ratio, window=a.window,
        full_scale_spl_db=a.full_scale_spl,
        tonal_offset_db=a.tonal_offset, noise_offset_db=a.noise_offset,
        spread_down_db_bark=a.spread_down, spread_up_db_bark=a.spread_up)


def _signal_kwargs(a) -> dict:
    kw = {"seed": a.seed}
    if a.kind == "tone":
        kw.update(tone_freq=a.tone_freq, tone_amp_dbfs=a.tone_amp)
    elif a.kind == "two_tone":
        kw.update(f1=a.f1, f2=a.f2, amp1_dbfs=a.amp1, amp2_dbfs=a.amp2)
    else:
        kw.update(noise_amp_dbfs=a.noise_amp)
    if a.probe_freq is not None:
        kw.update(probe_freq=a.probe_freq, probe_amp_dbfs=a.probe_amp)
    return kw


def print_report(result) -> None:
    t = result.thresholds
    print(f"{'Band':>4} {'fc(Hz)':>9} {'Signal':>9} {'ATH':>8} "
          f"{'Thresh':>9} {'Margin':>8}  status")
    for i, band in enumerate(BARK_BANDS):
        status = "audible" if t["audible_margin_db"][i] >= 0 else "masked"
        print(f"{band.index:4d} {band.center:9.0f} "
              f"{t['signal_level_db'][i]:9.2f} {t['ath_db'][i]:8.2f} "
              f"{t['global_threshold_db'][i]:9.2f} "
              f"{t['audible_margin_db'][i]:8.2f}  {status}")
    n_audible = int(np.sum(t["audible_margin_db"] >= 0))
    print(f"\ntonal maskers: {len(t['tonal_maskers'])}, "
          f"noise maskers: {len(t['noise_maskers'])}, "
          f"audible bands: {n_audible}/24")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    params = _params_from_args(args)

    if args.interactive:
        from .interactive import launch_interactive
        launch_interactive(params, args.duration)
        return 0

    if args.show_params:
        print("\n".join(params.summary_lines()))
        print()

    signal = make_signal(args.kind, params.fs, args.duration,
                         normalize=True, **_signal_kwargs(args))
    result = run_analysis(signal, params)
    print_report(result)

    if args.check_energy:
        check = band_energy_invariance(signal, params,
                                       n_fft_list=(1024, 2048, 4096, 8192))
        print("\n[FFT-length invariance of in-band energy]")
        for n_fft, row in zip(check["n_fft_list"], check["band_power"]):
            print(f"  n_fft={n_fft:5d}: total in-band energy = "
                  f"{10 * np.log10(np.sum(row) + 1e-20):8.4f} dB")
        print(f"  max  |delta| across bands = "
              f"{check['max_abs_diff_db']:.4f} dB")
        print(f"  mean |delta| across bands = "
              f"{check['mean_abs_diff_db']:.4f} dB")

    if args.plot:
        from .plotting import save_plot
        save_plot(result, args.plot,
                  f"Critical-band analysis: {args.kind}")
        print(f"\nplot saved: {args.plot}")

    print("\nNote: 结果仅为所实现规则的工程演示，"
          "不构成医学/临床听力结论。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
