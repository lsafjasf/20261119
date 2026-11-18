"""交互式探测音：滑块移动探测音频率与幅度，实时重算掩蔽阈值/可听余量。"""

from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider, RadioButtons

from .analysis import run_analysis
from .plotting import plot_result, DISCLAIMER
from .signals import make_signal


def launch_interactive(params, duration_s: float = 1.0) -> None:
    """启动 matplotlib 交互窗口（需要图形界面环境）。"""
    state = {"kind": "tone"}

    def build_signal(kind: str, probe_freq: float, probe_amp: float):
        kwargs = {"probe_freq": probe_freq, "probe_amp_dbfs": probe_amp}
        if kind == "tone":
            kwargs.update(tone_freq=1000.0, tone_amp_dbfs=-20.0)
        elif kind == "two_tone":
            kwargs.update(f1=980.0, f2=1020.0,
                          amp1_dbfs=-20.0, amp2_dbfs=-26.0)
        else:
            kwargs.update(noise_amp_dbfs=-20.0, seed=42)
        return make_signal(kind, params.fs, duration_s,
                           normalize=True, **kwargs)

    signal = build_signal(state["kind"], 1500.0, -30.0)
    result = run_analysis(signal, params)
    fig, axs = plt.subplots(2, 2, figsize=(13, 9))
    fig.subplots_adjust(bottom=0.18, right=0.82)

    def redraw():
        for ax in axs.flat:
            ax.clear()
        plot_result(result, f"Interactive probe - masker={state['kind']}",
                    axs=axs)

    redraw()

    ax_freq = fig.add_axes([0.12, 0.10, 0.55, 0.03])
    ax_amp = fig.add_axes([0.12, 0.05, 0.55, 0.03])
    ax_radio = fig.add_axes([0.86, 0.55, 0.12, 0.2])
    slider_freq = Slider(ax_freq, "Probe f (Hz)", 50.0,
                         params.fs / 2 * 0.9, valinit=1500.0)
    slider_amp = Slider(ax_amp, "Probe amp (dBFS)", -80.0, -3.0,
                        valinit=-30.0)
    radio = RadioButtons(ax_radio, ("tone", "two_tone", "noise"))

    def update(_=None):
        nonlocal result
        signal = build_signal(state["kind"],
                              slider_freq.val, slider_amp.val)
        result = run_analysis(signal, params)
        redraw()

    def switch(label):
        state["kind"] = label
        update()

    slider_freq.on_changed(update)
    slider_amp.on_changed(update)
    radio.on_clicked(switch)
    fig.text(0.5, 0.005, DISCLAIMER, ha="center", fontsize=9, color="dimgray")
    plt.show()
