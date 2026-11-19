"""Declared synthesis parameters and vowel presets.

All quantities are in SI/Hz units. The synthesizer only ever receives these
physical parameters; no learned speech model or recorded sample is used.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, replace

SAMPLE_RATE = 22050

# Physical safety limits. F1 is kept away from 0 and from Nyquist so the
# resonator poles never approach the unit circle or the real axis pathologies.
LIMITS = {
    "f0": (60.0, 500.0),
    "f1": (120.0, 1200.0),
    "bw1": (30.0, 600.0),
    "f2": (400.0, 3000.0),
    "bw2": (30.0, 600.0),
    "f3": (1200.0, 4500.0),
    "bw3": (30.0, 800.0),
    "breathiness": (0.0, 1.0),
    "smooth_tau": (0.005, 0.5),
}


@dataclass
class SynthParams:
    """User-declared control parameters.

    f0            fundamental frequency of the periodic glottal source (Hz)
    f1..f3        declared formant centre frequencies (Hz)
    bw1..bw3       declared formant -3 dB bandwidths (Hz)
    breathiness   mix coefficient: 0 = fully periodic, 1 = fully aspirated
    smooth_tau    one-pole smoothing time constant applied to every control
    """

    f0: float = 120.0
    f1: float = 730.0
    bw1: float = 80.0
    f2: float = 1090.0
    bw2: float = 90.0
    f3: float = 2440.0
    bw3: float = 120.0
    breathiness: float = 0.15
    smooth_tau: float = 0.05

    def to_dict(self) -> dict:
        return asdict(self)

    def updated(self, **changes) -> "SynthParams":
        return replace(self, **changes)


def clamp_params(p: SynthParams) -> SynthParams:
    """Clip every scalar into LIMITS and enforce ascending formant order."""
    values = {}
    for name, (lo, hi) in LIMITS.items():
        values[name] = float(min(max(getattr(p, name), lo), hi))
    # Keep formants strictly separated so cascaded sections never coincide.
    gap = 30.0
    values["f1"] = min(values["f1"], values["f2"] - gap)
    values["f2"] = min(max(values["f2"], values["f1"] + gap), values["f3"] - gap)
    values["f3"] = max(values["f3"], values["f2"] + gap)
    # Re-clip after ordering against absolute limits.
    values["f1"] = min(max(values["f1"], LIMITS["f1"][0]), LIMITS["f1"][1])
    values["f3"] = min(max(values["f3"], LIMITS["f3"][0]), LIMITS["f3"][1])
    values["f2"] = min(max(values["f2"], LIMITS["f2"][0]), LIMITS["f2"][1])
    return SynthParams(**values)


# Classical Peterson & Barney style target frequencies with bandwidths.
VOWEL_PRESETS = {
    "a": SynthParams(120, 730, 80, 1090, 90, 2440, 120, 0.15, 0.05),
    "i": SynthParams(120, 270, 70, 2290, 90, 3010, 140, 0.10, 0.05),
    "u": SynthParams(120, 300, 80, 870, 90, 2240, 130, 0.12, 0.05),
    "e": SynthParams(120, 530, 80, 1840, 100, 2480, 140, 0.12, 0.05),
    "o": SynthParams(120, 570, 90, 840, 100, 2410, 140, 0.18, 0.05),
    "whisper_a": SynthParams(120, 730, 100, 1090, 110, 2440, 150, 0.85, 0.05),
}
