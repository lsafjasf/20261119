"""Reproducible synthetic crossing scenario with frame-level ground truth."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .geometry import Point, Quad, overlap_fraction, quad_from_center, rectangle
from .image import GrayImage, add_noise, adjust_brightness, draw_rectangle
from .marker import render_marker

DEFAULT_MARKER_IDS = (7, 42)
MARKER_SIZE = 46.0


@dataclass(frozen=True)
class MarkerTruth:
    marker_id: int
    corners: Quad
    visible_fraction: float

    @property
    def visibility(self) -> str:
        if self.visible_fraction >= 0.78:
            return "visible"
        if self.visible_fraction > 0.10:
            return "partial"
        return "occluded"


@dataclass(frozen=True)
class FrameTruth:
    frame: int
    brightness: float
    markers: tuple[MarkerTruth, ...]


@dataclass
class SyntheticClip:
    width: int
    height: int
    images: list[GrayImage]
    truth: list[FrameTruth]
    marker_ids: tuple[int, int] = DEFAULT_MARKER_IDS

    def save(self, directory: str | Path) -> Path:
        root = Path(directory)
        frame_directory = root / "frames"
        frame_directory.mkdir(parents=True, exist_ok=True)
        for frame, image in enumerate(self.images):
            image.save_pgm(frame_directory / f"frame_{frame:04d}.pgm")
        payload = {
            "format": "watermark-crossing-v1",
            "width": self.width,
            "height": self.height,
            "frame_count": len(self.images),
            "marker_ids": list(self.marker_ids),
            "frames": [
                {
                    "frame": truth.frame,
                    "brightness": round(truth.brightness, 6),
                    "markers": [
                        {
                            "marker_id": marker.marker_id,
                            "corners": [
                                [round(point.x, 6), round(point.y, 6)]
                                for point in marker.corners.corners
                            ],
                            "visible_fraction": round(marker.visible_fraction, 6),
                            "visibility": marker.visibility,
                        }
                        for marker in truth.markers
                    ],
                }
                for truth in self.truth
            ],
        }
        destination = root / "ground_truth.json"
        destination.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return destination

    def write_avi(self, path: str | Path, fps: int = 25) -> None:
        from .avi import write_avi

        write_avi(path, self.images, fps=fps)


def _background(image: GrayImage, width: int, height: int) -> None:
    for y in range(height):
        for x in range(width):
            gradient = 18.0 * x / max(1, width - 1) + 8.0 * y / max(1, height - 1)
            image.data[y * width + x] = max(0, min(255, int(214 + gradient)))


def _occluder_for_frame(
    frame: int, a_center: Point, frame_count: int
) -> list[Point] | None:
    start = max(2, int(frame_count * 0.24))
    end = start + 4
    if not start <= frame < end:
        return None
    shift = (frame - start) * 2.0
    return rectangle(a_center.x + shift, a_center.y + 3.0, 56.0, 56.0)


def _marker_quad(center: Point, angle: float, perspective: float) -> Quad:
    return quad_from_center(center, MARKER_SIZE, angle, perspective)


def generate_crossing_clip(
    frame_count: int = 72,
    width: int = 360,
    height: int = 240,
    seed: int = 20261119,
    marker_ids: tuple[int, int] = DEFAULT_MARKER_IDS,
) -> SyntheticClip:
    images: list[GrayImage] = []
    truth: list[FrameTruth] = []
    left_id, right_id = marker_ids
    for frame in range(frame_count):
        progress = frame / max(1, frame_count - 1)
        phase = 2.0 * math.pi * progress
        a_center = Point(72.0 + 216.0 * progress, 97.0 + 2.0 * math.sin(phase))
        b_center = Point(288.0 - 216.0 * progress, 153.0 - 2.0 * math.sin(phase))
        a_quad = _marker_quad(
            a_center,
            0.10 * math.sin(1.5 * phase),
            0.10 * math.sin(2.0 * phase + 0.4),
        )
        b_quad = _marker_quad(
            b_center,
            -0.12 * math.sin(1.25 * phase + 0.3),
            0.08 * math.cos(1.75 * phase),
        )
        occluder = _occluder_for_frame(frame, a_center, frame_count)

        a_fraction = 1.0 - overlap_fraction(a_quad.corners, b_quad.corners)
        if occluder is not None:
            a_fraction -= overlap_fraction(a_quad.corners, occluder)
        a_fraction = max(0.0, min(1.0, a_fraction))
        b_fraction = 1.0
        if occluder is not None:
            b_fraction -= overlap_fraction(b_quad.corners, occluder)
        b_fraction = max(0.0, min(1.0, b_fraction))

        brightness = 0.74 + 0.23 * math.sin(phase - math.pi / 2.0)
        image = GrayImage(width, height)
        _background(image, width, height)
        render_marker(image, a_quad, left_id)
        if occluder is not None:
            draw_rectangle(
                image,
                min(point.x for point in occluder),
                min(point.y for point in occluder),
                max(point.x for point in occluder),
                max(point.y for point in occluder),
                186,
                texture=4.0,
                seed=seed + frame,
            )
        render_marker(image, b_quad, right_id)
        adjust_brightness(image, brightness)
        add_noise(image, 2.0, seed + frame * 97)
        images.append(image)
        truth.append(
            FrameTruth(
                frame=frame,
                brightness=brightness,
                markers=(
                    MarkerTruth(left_id, a_quad, a_fraction),
                    MarkerTruth(right_id, b_quad, b_fraction),
                ),
            )
        )
    return SyntheticClip(width, height, images, truth, marker_ids)


def load_frame_sequence(directory: str | Path) -> Iterator[GrayImage]:
    root = Path(directory)
    frame_directory = root / "frames"
    for path in sorted(frame_directory.glob("frame_*.pgm")):
        yield GrayImage.read_pgm(path)


def load_ground_truth(path: str | Path) -> list[FrameTruth]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    frames: list[FrameTruth] = []
    for frame_data in payload["frames"]:
        markers: list[MarkerTruth] = []
        for marker_data in frame_data["markers"]:
            markers.append(
                MarkerTruth(
                    marker_id=int(marker_data["marker_id"]),
                    corners=Quad.from_points(marker_data["corners"]),
                    visible_fraction=float(marker_data["visible_fraction"]),
                )
            )
        frames.append(
            FrameTruth(
                frame=int(frame_data["frame"]),
                brightness=float(frame_data["brightness"]),
                markers=tuple(markers),
            )
        )
    return frames
