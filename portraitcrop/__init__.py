"""Content-aware portrait cropping with constrained camera paths."""

from .models import AnnotationSet, Box, CropSpec, FrameAnnotation, Region
from .solver import solve_path

__all__ = [
    "AnnotationSet",
    "Box",
    "CropSpec",
    "FrameAnnotation",
    "Region",
    "solve_path",
]

__version__ = "0.1.0"
