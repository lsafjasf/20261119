"""Spatial tracking for error-corrected square digital watermarks."""

from .codec import MarkerPayload, decode_payload, encode_payload
from .detector import DetectedMarker, detect_markers
from .evaluation import evaluate_all
from .scenario import generate_crossing_clip
from .tracker import FrameObservation, Tracker, TrackerResult

__all__ = [
    "DetectedMarker",
    "FrameObservation",
    "MarkerPayload",
    "Tracker",
    "TrackerResult",
    "detect_markers",
    "decode_payload",
    "encode_payload",
    "evaluate_all",
    "generate_crossing_clip",
]

__version__ = "0.1.0"

