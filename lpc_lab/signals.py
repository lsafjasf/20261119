"""Deterministic synthetic speech probes used by the laboratory."""

from __future__ import annotations

import math
import random
from pathlib import Path

from .wavio import write_wav

SAMPLE_RATE = 16000


def _normalize(samples: list[float], peak: float) -> list[float]:
    maximum = max((abs(value) for value in samples), default=0.0)
    if maximum <= 0.0:
        return samples
    return [value * peak / maximum for value in samples]


def sustained_vowel(duration: float = 1.2) -> list[float]:
    """A voiced /a/-like signal with harmonics and a slow pitch contour."""

    count = int(SAMPLE_RATE * duration)
    formants = [(700.0, 90.0), (1220.0, 110.0), (2600.0, 160.0)]
    samples: list[float] = []
    rng = random.Random(20261119)
    for n in range(count):
        time = n / SAMPLE_RATE
        f0 = 118.0 + 4.0 * math.sin(2.0 * math.pi * 2.3 * time)
        value = 0.0
        harmonic = 1
        while harmonic * f0 < 4200.0:
            frequency = harmonic * f0
            envelope = 0.0
            for center, bandwidth in formants:
                envelope = max(
                    envelope,
                    1.0 / math.sqrt(1.0 + ((frequency - center) / bandwidth) ** 2),
                )
            value += envelope * math.sin(
                2.0 * math.pi * frequency * time + 0.13 * harmonic
            ) / harmonic
            harmonic += 1
        edge = min(1.0, time / 0.04, (duration - time) / 0.04)
        value = value * max(0.0, edge) + rng.uniform(-0.002, 0.002)
        samples.append(value)
    return _normalize(samples, 0.65)


def unvoiced_fricative(duration: float = 1.2) -> list[float]:
    """Noise-driven /s/-like high-frequency signal (no pitch pulse)."""

    count = int(SAMPLE_RATE * duration)
    rng = random.Random(220719)
    samples: list[float] = []
    previous_noise = 0.0
    highpass_state = 0.0
    for n in range(count):
        noise = rng.uniform(-1.0, 1.0)
        differentiated = noise - previous_noise
        previous_noise = noise
        highpass_state = 0.82 * highpass_state + 0.55 * differentiated
        time = n / SAMPLE_RATE
        edge = min(1.0, time / 0.025, (duration - time) / 0.025)
        samples.append(highpass_state * max(0.0, edge))
    return _normalize(samples, 0.32)


def digital_silence(duration: float = 0.8) -> list[float]:
    return [0.0] * int(SAMPLE_RATE * duration)


def write_probes(directory: str | Path) -> dict[str, Path]:
    """Write the three probes and return their paths."""

    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    probes = {
        "vowel": (sustained_vowel(), "持续元音 /a/"),
        "fricative": (unvoiced_fricative(), "无声摩擦音 /s/"),
        "silence": (digital_silence(), "数字静音（全零）"),
    }
    paths: dict[str, Path] = {}
    for name, (samples, _) in probes.items():
        path = output / f"{name}.wav"
        write_wav(path, samples, SAMPLE_RATE)
        paths[name] = path
    return paths
