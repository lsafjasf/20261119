"""Multi-exposure HDR merge with saturation weighting and linear irradiance output."""

from .response import estimate_response
from .merge import merge_exposures
from .synthetic import generate_test_sequence

__all__ = [
    "estimate_response",
    "merge_exposures",
    "generate_test_sequence",
]
