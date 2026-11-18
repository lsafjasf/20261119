"""Spectrogram + waveform comparison figures for the PLC simulation."""
import os
import wave
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import spectrogram

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "out")
FS = 16000
FRAME = 320

STRATS = ["silence", "repeat", "lpc_pred", "opus_builtin"]
CASES = {  # case -> (lost_frames, label)
    "iso_vowel_i": ([44], "isolated loss in /i/"),
    "burst3_vowel_u": ([74, 75, 76], "3-packet burst in /u/"),
    "burst3_fric_sh": ([60, 61, 62], "3-packet burst in /sh/"),
}


def rd(name):
    with wave.open(os.path.join(OUT, name), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()),
                             dtype=np.int16) / 32768.0


def spec(ax, x, title):
    f, t, S = spectrogram(x, FS, nperseg=128, noverlap=112)
    ax.pcolormesh(t * 1000, f / 1000, 10 * np.log10(S + 1e-12),
                  vmin=-120, vmax=-20, shading="auto", cmap="magma")
    ax.set_ylim(0, 8)
    ax.set_title(title, fontsize=9)
    ax.set_ylabel("kHz")


ref = rd("reference_lossless_decode.wav")

for case, (lost, label) in CASES.items():
    lo, hi = min(lost) * FRAME, (max(lost) + 1) * FRAME
    pad = 3 * FRAME
    sl = slice(max(0, lo - pad), min(len(ref), hi + pad))

    fig, axes = plt.subplots(5, 1, figsize=(10, 11), sharex=True)
    spec(axes[0], ref[sl], f"reference (no loss) -- {label}")
    for ax, s in zip(axes[1:], STRATS):
        spec(ax, rd(f"{case}__{s}.wav")[sl], s)
    for ax in axes:
        t0 = (max(0, lo - pad)) / FS * 1000
        for b in (lo, hi):
            ax.axvline(b / FS * 1000 - t0 + (pad if lo - pad < 0 else 0)
                       if False else (b - max(0, lo - pad)) / FS * 1000,
                       color="cyan", ls="--", lw=0.8)
    axes[-1].set_xlabel("ms")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, f"spectrogram_{case}.png"), dpi=110)
    plt.close(fig)

    # waveform zoom around the concealed region
    fig, axes = plt.subplots(5, 1, figsize=(10, 10), sharex=True)
    t_ms = (np.arange(sl.start, sl.stop) - lo) / FS * 1000
    for ax, sig, name in zip(axes, [ref] + [rd(f"{case}__{s}.wav")
                                            for s in STRATS],
                             ["reference"] + STRATS):
        ax.plot(t_ms, sig[sl], lw=0.6)
        ax.axvspan(0, (hi - lo) / FS * 1000, color="red", alpha=0.12)
        ax.set_ylabel(name, fontsize=8, rotation=0, labelpad=28)
        ax.set_ylim(-0.8, 0.8)
        ax.grid(alpha=0.3)
    axes[-1].set_xlabel("ms relative to loss start (red = concealed)")
    fig.suptitle(f"waveform zoom -- {label}")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, f"waveform_{case}.png"), dpi=110)
    plt.close(fig)
    print("plots:", case)
