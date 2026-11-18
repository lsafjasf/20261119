"""Deterministic test signals and level measurements (stdlib only)."""

import math


def sine_wave(length, frequency, sample_rate, phase=0.0, amplitude=1.0):
    """In-phase, identical sine wave used by both crossfaded sources."""
    return [
        amplitude * math.sin(
            2.0 * math.pi * frequency * n / sample_rate + phase)
        for n in range(length)
    ]


def white_noise(length, seed, amplitude=1.0):
    """Deterministic LCG white noise; different seeds are uncorrelated."""
    state = seed
    out = []
    for _ in range(length):
        # Numerical Recipes LCG, mapped to roughly [-amplitude, amplitude).
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        out.append(amplitude * (2.0 * state / 0x100000000 - 1.0))
    return out


def ramp(length, start=0.0, step=1.0):
    """Distinctive ramp signal whose sample values encode their position."""
    return [start + i * step for i in range(length)]


def peak_abs(samples):
    return max((abs(s) for s in samples), default=0.0)


def rms(samples):
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def to_db(ratio):
    """Level ratio in dB; ``0.0`` maps to negative infinity via a guard."""
    if ratio <= 0.0:
        return float("-inf")
    return 20.0 * math.log10(ratio)


def crossfade_sum(samples, fade_length, mode, fade_module):
    """Sum two identical-length sources over a tail/head overlap.

    Source A occupies samples ``[0, len)``, source B starts at
    ``len - fade_length``; only the overlap region is returned.  This mirrors
    exactly how the timeline renderer mixes an outgoing and incoming clip.
    """
    out_gains, in_gains = fade_module.fade_gains(mode, fade_length)
    offset = len(samples) - fade_length
    return [
        samples[offset + i] * out_gains[i]
        + samples[i] * in_gains[i]
        for i in range(fade_length)
    ]


def crossfade_sum_pair(source_a, source_b, fade_length, mode, fade_module):
    """Crossfade two distinct (uncorrelated) sources over their overlap."""
    out_gains, in_gains = fade_module.fade_gains(mode, fade_length)
    offset = len(source_a) - fade_length
    return [
        source_a[offset + i] * out_gains[i]
        + source_b[i] * in_gains[i]
        for i in range(fade_length)
    ]


def central_window(samples, fraction=0.1):
    """Central fraction of a region, where the fade midpoint lives."""
    half = max(1, int(len(samples) * fraction / 2.0))
    middle = len(samples) // 2
    return samples[middle - half:middle + half]
