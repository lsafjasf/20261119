"""Continuous pitch-preserving time stretching."""

from .core import (
    StretchConfig,
    StretchInfo,
    StretchResult,
    stretch,
    stretch_offline,
    stretch_with_info,
)
from .schedule import SpeedCurve, SpeedSchedule

__all__ = [
    "SpeedCurve",
    "SpeedSchedule",
    "StretchConfig",
    "StretchInfo",
    "StretchResult",
    "stretch",
    "stretch_offline",
    "stretch_with_info",
]
