"""Glottal-source formant synthesizer (no speech models, no recordings)."""

from .params import SynthParams, VOWEL_PRESETS, clamp_params
from .engine import VowelSynthesizer

__all__ = ["SynthParams", "VOWEL_PRESETS", "clamp_params", "VowelSynthesizer"]
