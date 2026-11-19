"""Visualization: harmonic explanation, unexplained spectrum and F0 tracks.

Produces two figures:
  * frame_explanation.png - magnitude spectrum of representative frames,
    with accepted harmonics shaded (explained) and residual peaks visible;
  * f0_tracks.png - per-frame candidate F0 with confidence, against the
    independent ground truth for visual sanity checking.
"""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import soundfile as sf

from .estimator import FRAME_MS, estimate_file, extract_peaks
from .evaluation import ground_truth_f0_at


def plot_frame_explanation(
    audio: np.ndarray,
    fs: int,
    results: list,
    times: list[float],
    out_path: str,
) -> None:
    n_fft = int(round(FRAME_MS / 1000.0 * fs))
    fig, axes = plt.subplots(len(times), 1, figsize=(11, 3.0 * len(times)), sharex=True)
    if len(times) == 1:
        axes = [axes]
    for ax, tc in zip(axes, times):
        idx = int(np.argmin([abs(r.time - tc) for r in results]))
        r = results[idx]
        start = int(r.time * fs) - n_fft // 2
        frame = audio[start : start + n_fft] * np.hanning(n_fft)
        spec = np.abs(np.fft.rfft(frame))
        freqs = np.fft.rfftfreq(n_fft, 1.0 / fs)
        peak_f, peak_m = extract_peaks(spec, freqs)
        ax.plot(freqs, spec, color="0.6", lw=0.8, label="spectrum")
        ax.plot(freqs, r.explained, color="tab:green", lw=1.2, label="explained")
        ax.plot(freqs, np.maximum(r.residual, 0), color="tab:red", lw=0.9, label="unexplained")
        for k, f0 in enumerate(r.f0):
            ax.axvline(f0, color=f"C{k}", ls="--", lw=1.0)
            ax.text(f0, 0.95 * spec.max(), f"F0={f0:.1f}\nc={r.confidence[k]:.2f}",
                    color=f"C{k}", fontsize=8, va="top")
        ax.set_title(f"t = {r.time:.2f}s, {len(r.f0)} voice(s)")
        ax.set_xlim(0, min(3500, fs / 2))
        ax.set_ylabel("magnitude")
        ax.legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel("frequency (Hz)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def plot_tracks(
    results: list,
    gt_path: str,
    out_path: str,
) -> None:
    with open(gt_path, encoding="utf-8") as fh:
        gt = json.load(fh)
    t = np.array([r.time for r in results])
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    for r in results:
        for f0, c in zip(r.f0, r.confidence):
            ax1.scatter(r.time, f0, s=12 + 40 * c, color="tab:blue", alpha=0.6)
    gt_t = np.linspace(0, gt["duration"], 800)
    for src in gt["sources"]:
        ys = []
        for tt in gt_t:
            vals = ground_truth_f0_at(gt, tt)
            # plot each source by matching its static value / range
            if src["kind"] == "static":
                ys.append(src["f0"] if src["active"][0] <= tt <= src["active"][1] else np.nan)
            else:
                a, b = src["active"]
                if a <= tt <= b:
                    lo, hi = src["f0"]
                    ys.append(lo + (hi - lo) * (tt - a) / (b - a))
                else:
                    ys.append(np.nan)
        ax1.plot(gt_t, ys, color="tab:red", ls="--", lw=1.2, label=src["name"])
    ax1.set_ylabel("F0 (Hz)")
    ax1.set_title("Estimated candidate F0 (blue, size=confidence) vs ground truth (red)")
    ax1.legend(fontsize=7, ncol=2)
    for r in results:
        for c in r.confidence:
            ax2.scatter(r.time, c, s=14, color="tab:purple", alpha=0.6)
    ax2.set_ylabel("confidence")
    ax2.set_xlabel("time (s)")
    ax2.set_ylim(0, 1)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def make_figures(wav_path: str, gt_path: str, max_voices: int) -> None:
    audio, fs = sf.read(wav_path)
    results, _ = estimate_file(audio, fs, max_voices=max_voices)
    plot_frame_explanation(
        audio, fs, results,
        times=[1.0, 3.0, 5.0, 6.0],
        out_path="results/frame_explanation.png",
    )
    plot_tracks(results, gt_path, "results/f0_tracks.png")
