"""Sample-accurate audio clip editor with overlap crossfades.

Modules:
    fade    - per-sample fade curve math (linear / equal-power)
    model   - timeline model: sources, clips, automation points, crossfades
    wavfile - minimal 16-bit PCM WAV reader/writer (stdlib only)
    render  - single sample-accurate renderer shared by audition and export
"""

from .fade import LINEAR, EQUAL_POWER, fade_curve, fade_gains
from .model import Source, Clip, AutomationPoint, Crossfade, Project
from .render import render_range, export_wav, audition
from .wavfile import write_wav, read_wav

__all__ = [
    "LINEAR",
    "EQUAL_POWER",
    "fade_curve",
    "fade_gains",
    "Source",
    "Clip",
    "AutomationPoint",
    "Crossfade",
    "Project",
    "render_range",
    "export_wav",
    "audition",
    "write_wav",
    "read_wav",
]
