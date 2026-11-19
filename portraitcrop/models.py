"""Core data types shared by the solver, diagnostics, and exporters."""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from math import floor
from typing import Any


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2.0

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2.0

    def clip(self, width: float, height: float) -> "Box":
        left = min(max(self.x, 0.0), width)
        top = min(max(self.y, 0.0), height)
        right = min(max(self.right, 0.0), width)
        bottom = min(max(self.bottom, 0.0), height)
        return Box(left, top, max(0.0, right - left), max(0.0, bottom - top))

    def union(self, other: "Box") -> "Box":
        left = min(self.x, other.x)
        top = min(self.y, other.y)
        right = max(self.right, other.right)
        bottom = max(self.bottom, other.bottom)
        return Box(left, top, right - left, bottom - top)

    def to_dict(self) -> dict[str, float]:
        return {
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "w": round(self.width, 3),
            "h": round(self.height, 3),
        }


@dataclass(frozen=True)
class Region:
    id: str
    box: Box
    confidence: float = 1.0
    label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            **self.box.to_dict(),
            "confidence": round(self.confidence, 4),
        }
        if self.label:
            result["label"] = self.label
        if self.metadata:
            result["metadata"] = self.metadata
        return result


@dataclass
class FrameAnnotation:
    index: int
    time: float
    regions: list[Region] = field(default_factory=list)


@dataclass
class AnnotationSet:
    width: int
    height: int
    fps: float
    frames: list[FrameAnnotation]
    source: str | None = None

    @property
    def duration(self) -> float:
        return len(self.frames) / self.fps if self.fps > 0 else 0.0

    def frame(self, index: int) -> FrameAnnotation:
        return self.frames[index]


@dataclass(frozen=True)
class CropSpec:
    source_width: int
    source_height: int
    width: int
    height: int
    y: int = 0

    @property
    def x_min(self) -> int:
        return 0

    @property
    def x_max(self) -> int:
        return self.source_width - self.width

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height

    def box_at(self, x: float) -> Box:
        return Box(float(x), float(self.y), float(self.width), float(self.height))

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("crop dimensions must be positive")
        if self.width > self.source_width or self.height > self.source_height:
            raise ValueError("crop window is larger than the source frame")
        if self.y < 0 or self.y + self.height > self.source_height:
            raise ValueError("crop y range is outside the source frame")


def _parse_aspect(aspect: str | float) -> tuple[float, float]:
    if isinstance(aspect, (int, float)):
        return float(aspect), 1.0
    text = str(aspect).strip().replace("x", ":").replace("/", ":")
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError(f"invalid aspect ratio: {aspect!r}")
    numerator, denominator = float(parts[0]), float(parts[1])
    if numerator <= 0 or denominator <= 0:
        raise ValueError("aspect ratio terms must be positive")
    return numerator, denominator


def _even_floor(value: int) -> int:
    return max(2, value if value % 2 == 0 else value - 1)


def make_crop_spec(
    source_width: int,
    source_height: int,
    aspect: str | float = "9:16",
    crop_width: int | None = None,
    crop_height: int | None = None,
    y: int | None = None,
) -> CropSpec:
    """Build an even-sized fixed-height crop window for a landscape source."""
    aw, ah = _parse_aspect(aspect)
    if crop_height is None:
        required_width = int(round(source_height * aw / ah))
        crop_height = source_height if required_width <= source_width else int(
            floor(source_width * ah / aw)
        )
    crop_height = _even_floor(int(crop_height))
    if crop_width is None:
        crop_width = int(round(crop_height * aw / ah))
    crop_width = _even_floor(int(crop_width))
    if crop_width > source_width or crop_height > source_height:
        raise ValueError(
            f"crop {crop_width}x{crop_height} does not fit source "
            f"{source_width}x{source_height}"
        )
    crop_y = (source_height - crop_height) // 2 if y is None else int(y)
    return CropSpec(source_width, source_height, crop_width, crop_height, crop_y)


@dataclass(frozen=True)
class Pin:
    frame: int
    x: float | None = None
    region_ids: tuple[str, ...] = ()
    tolerance: float = 0.0
    hard: bool = True
    align: str = "center"
    note: str = ""


@dataclass(frozen=True)
class ResolvedPin:
    pin: Pin
    target_x: float


@dataclass
class Conflict:
    kind: str
    frame: int
    time: float
    region_ids: tuple[str, ...]
    required_span: float
    available_span: float
    overrun: float
    union_box: Box | None = None
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "kind": self.kind,
            "frame": self.frame,
            "time": round(self.time, 4),
            "region_ids": list(self.region_ids),
            "required_span": round(self.required_span, 3),
            "available_span": round(self.available_span, 3),
            "overrun": round(self.overrun, 3),
            "confidence": round(self.confidence, 4),
        }
        if self.union_box:
            result["union_box"] = self.union_box.to_dict()
        return result


@dataclass
class Window:
    frame: int
    time: float
    x: int
    y: int
    width: int
    height: int
    coverage: float
    velocity_px_s: float = 0.0
    acceleration_px_s2: float = 0.0

    @property
    def right(self) -> int:
        return self.x + self.width

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame": self.frame,
            "time": round(self.time, 6),
            "x": self.x,
            "y": self.y,
            "w": self.width,
            "h": self.height,
            "coverage": round(self.coverage, 6),
            "velocity_px_s": round(self.velocity_px_s, 4),
            "acceleration_px_s2": round(self.acceleration_px_s2, 4),
        }


@dataclass
class SolveResult:
    status: str
    crop: CropSpec
    fps: float
    windows: list[Window]
    total_cost: float | None = None
    failed_frame: int | None = None
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    pins: list[Pin] = field(default_factory=list)
    resolved_pins: list[ResolvedPin] = field(default_factory=list)


def fps_fraction(fps: float | str | Fraction) -> Fraction:
    if isinstance(fps, Fraction):
        return fps
    if isinstance(fps, str) and "/" in fps:
        return Fraction(fps)
    return Fraction(str(fps)).limit_denominator(100000)
