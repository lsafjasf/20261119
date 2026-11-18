"""绘图：静态分析图与交互式探测音（matplotlib 滑块）。

图中文字使用英文，避免无中文字体环境下的乱码；模型用途限制
  （仅工程演示，不构成医学听力结论）同时标注在图脚与中文文档中。
"""

from __future__ import annotations

import numpy as np
import matplotlib

from .analysis import run_analysis
from .bark import BARK_BANDS
from .signals import make_signal

DISCLAIMER = ("Demo of implemented rules only - not a medical/hearing "
              "diagnosis.")


def plot_result(result, title: str = "Critical-band analysis",
                axs=None) -> object:
    """绘制 2x2 分析面板；传入 axs 时复用给定坐标轴。"""
    if axs is None:
        fig, axs = matplotlib.pyplot.subplots(2, 2, figsize=(13, 9))
    else:
        fig = axs[0, 0].figure
    centers = result.band_centers
    p = result.params

    # (1) 时域波形
    ax = axs[0, 0]
    t = np.arange(len(result.signal)) / p.fs
    ax.plot(t, result.signal, lw=0.7)
    ax.set_title("Time waveform")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Amplitude")
    ax.grid(alpha=0.3)

    # (2) 频谱 + ATH + 全局掩蔽阈值 + 掩蔽声
    ax = axs[0, 1]
    freqs = result.frames.freqs
    bin_spl = result.mean_power_db + p.full_scale_spl_db
    ax.semilogx(freqs[1:], bin_spl[1:], lw=0.8, color="steelblue",
                label="Signal spectrum")
    ax.semilogx(centers, result.thresholds["ath_db"], "k--", lw=1.2,
                label="Absolute threshold (Terhardt)")
    ax.step(centers, result.thresholds["global_threshold_db"], where="mid",
            color="crimson", lw=1.6, label="Global masking threshold")
    for m in result.thresholds["tonal_maskers"]:
        ax.plot(m.freq_hz, m.level_db_spl, "^", color="darkorange", ms=7)
    ax.set_title("Spectrum and masking thresholds")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("dB SPL")
    ax.set_xlim(20, p.fs / 2)
    ax.set_ylim(-20, max(110, np.max(bin_spl) + 10))
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, which="both")

    # (3) 可听余量柱状图
    ax = axs[1, 0]
    margin = result.thresholds["audible_margin_db"]
    colors = ["#d9534f" if v >= 0 else "#5b9bd5" for v in margin]
    ax.bar(np.arange(1, 25), margin, color=colors)
    ax.axhline(0, color="black", lw=1)
    ax.set_title("Audible margin per Bark band (red=audible, blue=masked)")
    ax.set_xlabel("Bark band index")
    ax.set_ylabel("Margin (dB)")
    ax.set_xticks(np.arange(1, 25, 2))
    ax.grid(alpha=0.3, axis="y")

    # (4) Bark 表与参数
    ax = axs[1, 1]
    ax.axis("off")
    shown = (0, 8, 23)
    lines = [f"Bark band {BARK_BANDS[i].index:2d}: "
             f"{BARK_BANDS[i].lower:7.0f}-{BARK_BANDS[i].upper:6.0f} Hz "
             f"(fc={BARK_BANDS[i].center:6.0f})" for i in shown]
    lines += ["  ... (24 bands total, full table in bark.py)",
              "", *p.summary_lines()]
    ax.text(0.0, 1.0, "\n".join(lines), va="top", ha="left",
            family="monospace", fontsize=8)
    ax.set_title("Bark map and model parameters")

    fig.suptitle(title, fontsize=13)
    fig.text(0.5, 0.01, DISCLAIMER, ha="center", fontsize=9, color="dimgray")
    fig.tight_layout(rect=(0, 0.035, 1, 0.96))
    return fig


def save_plot(result, path: str, title: str = "Critical-band analysis") -> None:
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt
    fig = plot_result(result, title)
    fig.savefig(path, dpi=130)
    plt.close(fig)
