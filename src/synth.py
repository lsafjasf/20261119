"""Synthetic polyphonic material generator.

Creates a deterministic mix with three documented scenarios:
  1. octave-related voices (lower voice with a MISSING fundamental),
  2. two glissando voices that cross,
  3. three voices where one stops abruptly.

Ground truth is written to a standalone JSON file so that the estimator
never imports generation parameters.
"""

from __future__ import annotations

import json

import numpy as np
import soundfile as sf

FS = 16000


def _fades(n: int, fade: int) -> np.ndarray:
    env = np.ones(n)
    k = min(fade, n // 2)
    ramp = 0.5 * (1.0 - np.cos(np.pi * np.arange(k) / k))
    env[:k] = ramp
    env[-k:] = ramp[::-1]
    return env


def render_voice(
    t: np.ndarray,
    f0_fn,
    active: tuple[float, float],
    rng: np.random.Generator,
    rolloff: float = 0.9,
    missing_fundamental: bool = False,
) -> np.ndarray:
    """Render one harmonic voice via integrated instantaneous frequency."""
    n = t.size
    dt = 1.0 / FS
    f0 = np.clip(f0_fn(t), 40.0, FS / 2 - 100.0)
    out = np.zeros(n)
    n_harm = int((FS / 2.0) // max(f0.min(), 1.0))
    for h in range(1, n_harm + 1):
        if missing_fundamental and h == 1:
            continue
        inst_freq = h * f0
        phase = 2.0 * np.pi * np.cumsum(inst_freq) * dt
        phase += rng.uniform(0.0, 2.0 * np.pi)
        amp = h ** (-rolloff) * (0.85 + 0.3 * rng.random())
        out += amp * np.cos(phase)
    env = np.zeros(n)
    i0, i1 = (np.array(active) * FS).astype(int)
    i0, i1 = max(0, i0), min(n, i1)
    if i1 > i0:
        env[i0:i1] = _fades(i1 - i0, int(0.03 * FS))
    return out * env


def build_mix(out_wav: str, out_gt: str, seed: int = 20261119) -> dict:
    rng = np.random.default_rng(seed)
    duration = 6.6
    t = np.arange(int(duration * FS)) / FS

    sources = [
        {
            "name": "oct_low_missingF0",
            "kind": "static",
            "f0": 220.0,
            "active": [0.0, 2.2],
            "missing_fundamental": True,
        },
        {
            "name": "oct_high",
            "kind": "static",
            "f0": 440.0,
            "active": [0.0, 2.2],
            "missing_fundamental": False,
        },
        {
            "name": "glide_down",
            "kind": "linear",
            "f0": [320.0, 180.0],
            "active": [2.2, 4.4],
            "missing_fundamental": False,
        },
        {
            "name": "glide_up",
            "kind": "linear",
            "f0": [180.0, 320.0],
            "active": [2.2, 4.4],
            "missing_fundamental": False,
        },
        {
            "name": "triple_low",
            "kind": "static",
            "f0": 150.0,
            "active": [4.4, 6.6],
            "missing_fundamental": False,
        },
        {
            "name": "triple_mid",
            "kind": "static",
            "f0": 261.63,
            "active": [4.4, 6.6],
            "missing_fundamental": False,
        },
        {
            "name": "triple_stop",
            "kind": "static",
            "f0": 392.0,
            "active": [4.4, 5.5],
            "missing_fundamental": False,
        },
    ]

    mix = np.zeros_like(t)
    for src in sources:
        if src["kind"] == "static":
            f0_fn = lambda tt, f=src["f0"]: np.full_like(tt, f)
        else:
            a, b = src["active"]
            f_lo, f_hi = src["f0"]

            def f0_fn(tt, a=a, b=b, f_lo=f_lo, f_hi=f_hi):
                p = np.clip((tt - a) / (b - a), 0.0, 1.0)
                return f_lo + (f_hi - f_lo) * p

        mix += render_voice(
            t,
            f0_fn,
            tuple(src["active"]),
            rng,
            missing_fundamental=src["missing_fundamental"],
        )

    # Additive measurement noise at a fixed SNR.
    target_snr_db = 35.0
    sig_power = np.mean(mix**2)
    noise_power = sig_power / (10.0 ** (target_snr_db / 10.0))
    mix += rng.normal(0.0, np.sqrt(noise_power), size=mix.shape)

    peak = np.max(np.abs(mix))
    mix = mix / peak * 0.9
    sf.write(out_wav, mix.astype(np.float32), FS)

    ground_truth = {"fs": FS, "duration": duration, "sources": sources}
    with open(out_gt, "w", encoding="utf-8") as fh:
        json.dump(ground_truth, fh, indent=2, ensure_ascii=False)
    return ground_truth


if __name__ == "__main__":
    build_mix("results/mix.wav", "results/ground_truth.json")
    print("wrote results/mix.wav and results/ground_truth.json")
