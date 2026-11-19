"""Matplotlib dashboard: envelopes, gain reduction, clips, recovery."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .analysis import (
    detect_hits,
    hit_peak_levels,
    recovery_curve,
    time_to_recover,
)
from .core import LimitResult, PeakLimiter


def _time_axis(x: np.ndarray, sr: float) -> np.ndarray:
    return np.arange(len(x)) / sr


def build_dashboard(
    x_drums: np.ndarray,
    x_bass: np.ndarray,
    sr: float,
    limiter: PeakLimiter,
) -> plt.Figure:
    """Six-panel comparison of bypass / fixed / auto modes."""
    modes = ("bypass", "fixed", "auto")
    colors = {"bypass": "#888888", "fixed": "#1f77b4", "auto": "#d62728"}
    results_d = {m: limiter.process(x_drums, m) for m in modes}
    results_b = {m: limiter.process(x_bass, m) for m in modes}

    fig, axes = plt.subplots(3, 2, figsize=(15, 11))
    fig.suptitle("Auto-Release Peak Limiter — Dashboard", fontsize=14, fontweight="bold")

    # 1. Input/output envelopes (drums, auto mode)
    ax = axes[0, 0]
    t = _time_axis(x_drums, sr)
    r = results_d["auto"]
    ax.plot(t, x_drums, color=colors["bypass"], lw=0.6, alpha=0.5, label="input")
    ax.plot(t, r.output, color=colors["auto"], lw=0.7, label="output (auto)")
    ax.axhline(limiter.cfg.ceiling_linear, color="k", ls="--", lw=1, label="ceiling")
    ax.axhline(-limiter.cfg.ceiling_linear, color="k", ls="--", lw=1)
    ax.set_title("Input / output envelope (drum train, auto)")
    ax.set_xlabel("time [s]")
    ax.legend(loc="lower right", fontsize=8)

    # 2. Gain reduction, all modes, drums
    ax = axes[0, 1]
    for m in modes:
        gr = -20 * np.log10(np.maximum(results_d[m].gain, 1e-10))
        ax.plot(t, gr, color=colors[m], lw=0.9, label=m)
    ax.set_title("Gain reduction (drum train)")
    ax.set_xlabel("time [s]")
    ax.set_ylabel("reduction [dB]")
    ax.legend(fontsize=8)

    # 3. Short-time clamp counts, drums
    ax = axes[1, 0]
    width = 0.012
    for i, m in enumerate(("fixed", "auto")):
        counts = limiter.short_term_clip_counts(results_d[m], window_ms=50.0)
        tt = np.arange(len(counts)) * 0.05
        ax.bar(tt + i * width, counts, width=width, color=colors[m], label=m)
    ax.set_title("Short-term safety-clamp activations (50 ms windows)")
    ax.set_xlabel("time [s]")
    ax.set_ylabel("clamped samples")
    ax.legend(fontsize=8)

    # 4. Hit-relative-level comparison
    ax = axes[1, 1]
    onsets = detect_hits(x_drums, sr)
    in_lvl = hit_peak_levels(x_drums, onsets, sr)
    idx = np.arange(len(in_lvl))
    ax.plot(idx, in_lvl, "o-", color=colors["bypass"], label="input", lw=1)
    for m in ("fixed", "auto"):
        lvl = hit_peak_levels(results_d[m].output, onsets, sr)
        ax.plot(idx, lvl, "s-", color=colors[m], label=m, lw=1)
    ax.set_title("Per-hit peak levels (dynamic shape)")
    ax.set_xlabel("hit index")
    ax.set_ylabel("peak [dB]")
    ax.legend(fontsize=8)

    # 5. Recovery curves: drums vs bass, auto mode
    ax = axes[2, 0]
    for name, results in (("drum train", results_d), ("sust. bass", results_b)):
        # Anchor the curve at the deepest gain-reduction point.
        trig = int(np.argmin(results["auto"].gain))
        for m, ls, base_color in (
            ("fixed", "--", colors["fixed"]),
            ("auto", "-", colors["auto"]),
        ):
            tt, red = recovery_curve(results[m].gain, trig, sr)
            ax.plot(
                tt,
                red,
                ls,
                color=base_color,
                lw=1.2,
                alpha=1.0 if name == "drum train" else 0.55,
                label=f"{name} / {m}",
            )
    ax.set_title("Gain recovery after a peak")
    ax.set_xlabel("time after peak [ms]")
    ax.set_ylabel("reduction [dB]")
    ax.legend(fontsize=7, ncol=2)

    # 6. Auto release time constant trace
    ax = axes[2, 1]
    tau_d = limiter._auto_release_tau(results_d["auto"].raw_gain)
    tau_b = limiter._auto_release_tau(results_b["auto"].raw_gain)
    ax.plot(t, tau_d, color=colors["auto"], lw=0.9, label="drum train")
    ax.plot(_time_axis(x_bass, sr), tau_b, color="#2ca02c", lw=0.9, label="sust. bass")
    ax.set_title("Program-dependent release time constant (auto)")
    ax.set_xlabel("time [s]")
    ax.set_ylabel("release tau [ms]")
    ax.legend(fontsize=8)

    fig.tight_layout(rect=[0, 0, 1, 0.97])
    return fig
