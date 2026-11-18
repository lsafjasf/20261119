"""Automatic notch feedback suppressor (simulated closed-loop only)."""

from .biquad import BiquadFilter, NotchBank, design_notch
from .plant import FeedbackPlant, PlantConfig
from .detector import HowlDetector, Detection, DetectorConfig
from .controller import NotchController, ControllerConfig

__all__ = [
    "BiquadFilter",
    "NotchBank",
    "design_notch",
    "FeedbackPlant",
    "PlantConfig",
    "HowlDetector",
    "Detection",
    "DetectorConfig",
    "NotchController",
    "ControllerConfig",
]
