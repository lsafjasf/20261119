"""Synthetic test material: drum-hit pulse train and sustained bass."""

from __future__ import annotations

import numpy as np


def _drum_hit(sr: float, freq: float, decay_ms: float, length: int) -> np.ndarray:
    """One percussive hit: fast attack, exponential decay, pitch glide."""
    t = np.arange(length) / sr
    attack = np.clip(t / 0.002, 0.0, 1.0)
    decay = np.exp(-t / (decay_ms / 1000.0))
    glide = freq * (1.0 + 0.25 * np.exp(-t / 0.03))
    phase = 2.0 * np.pi * np.cumsum(glide) / sr
    return attack * decay * np.sin(phase)


def drum_pulse_train(
    sr: float = 48_000.0,
    duration_s: float = 4.0,
    hit_hz: float = 4.0,
    levels_db: tuple[float, ...] = (0.0, -6.0, -3.0, -9.0, -2.0),
    seed: int = 7,
) -> np.ndarray:
    """Periodic drum hits with deliberately unequal strike levels.

    Five level classes repeat across the sequence so the limiter's
    preservation of *relative* inter-hit dynamics can be measured.
    """
    rng = np.random.default_rng(seed)
    n = int(round(duration_s * sr))
    x = np.zeros(n)
    period = int(round(sr / hit_hz))
    hit_len = int(round(0.18 * sr))
    k = 0
    for start in range(0, n - hit_len, period):
        level = 10.0 ** (levels_db[k % len(levels_db)] / 20.0)
        jitter = int(rng.integers(-int(0.004 * sr), int(0.004 * sr)))
        s = max(0, start + jitter)
        hit = _drum_hit(sr, 90.0 + 10.0 * (k % 3), 70.0, hit_len)
        x[s : s + hit_len] += level * hit
        k += 1
    x /= max(np.max(np.abs(x)), 1e-12)
    return x


def sustained_bass(
    sr: float = 48_000.0,
    duration_s: float = 4.0,
    freq_hz: float = 55.0,
) -> np.ndarray:
    """Continuous low-frequency tone with a slow amplitude swell.

    Stays above the limiter ceiling most of the time, which stresses the
    release/recovery behaviour under constant gain reduction.
    """
    n = int(round(duration_s * sr))
    t = np.arange(n) / sr
    swell = 0.75 + 0.25 * np.sin(2.0 * np.pi * 0.5 * t - np.pi / 2)
    tone = np.sin(2.0 * np.pi * freq_hz * t)
    tone += 0.3 * np.sin(2.0 * np.pi * 2 * freq_hz * t)
    x = swell * tone
    x /= max(np.max(np.abs(x)), 1e-12)
    return x


def drum_plus_bass(
    sr: float = 48_000.0, duration_s: float = 4.0, mix_db: float = -3.0
) -> np.ndarray:
    """Dense mix: pulse train over a sustained bass bed."""
    drums = drum_pulse_train(sr, duration_s)
    bass = sustained_bass(sr, duration_s) * (10.0 ** (mix_db / 20.0))
    x = drums + bass
    x /= max(np.max(np.abs(x)), 1e-12)
    return x
