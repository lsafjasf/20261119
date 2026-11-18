"""A small, dependency-free LPC speech coding laboratory."""

from .dsp import (
    AnalysisConfig,
    analyze_frame,
    analyze_signal,
    autocorrelation,
    levinson_durbin,
    pole_radius,
    polynomial_roots,
)

__all__ = [
    "AnalysisConfig",
    "analyze_frame",
    "analyze_signal",
    "autocorrelation",
    "levinson_durbin",
    "pole_radius",
    "polynomial_roots",
]

