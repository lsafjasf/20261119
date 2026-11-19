"""Deterministic synthetic scenarios for dual crossing and sudden entry."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .io import write_json
from .media import encode_raw_frames
from .models import Box, Region, make_crop_spec


@dataclass
class SubjectTrack:
    id: str
    label: str
    color: tuple[int, int, int]
    box_at: Callable[[float], Box | None]
    confidence_at: Callable[[float], float]

    def region_at(self, time_s: float) -> Region | None:
        box = self.box_at(time_s)
        if box is None:
            return None
        return Region(
            id=self.id,
            label=self.label,
            box=box,
            confidence=min(1.0, max(0.0, self.confidence_at(time_s))),
        )


@dataclass
class Scenario:
    name: str
    title: str
    width: int
    height: int
    fps: int
    duration_s: float
    subjects: list[SubjectTrack]
    pins: list[dict[str, object]]
    max_speed_px_s: float = 420.0
    max_accel_px_s2: float = 1400.0

    @property
    def frame_count(self) -> int:
        return int(round(self.duration_s * self.fps))

    def regions_at(self, time_s: float) -> list[Region]:
        regions = [track.region_at(time_s) for track in self.subjects]
        return [region for region in regions if region is not None]

    def annotation_payload(self) -> dict[str, object]:
        frames = []
        for index in range(self.frame_count):
            time_s = index / self.fps
            frames.append(
                {
                    "frame": index,
                    "time": round(time_s, 6),
                    "regions": [
                        region.to_dict() for region in self.regions_at(time_s)
                    ],
                }
            )
        return {
            "version": 1,
            "scenario": self.name,
            "title": self.title,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "frames": frames,
        }

    def run_config(self) -> dict[str, object]:
        crop = make_crop_spec(self.width, self.height, aspect="9:16")
        return {
            "video": "source.mp4",
            "rois": "rois.json",
            "pins": "pins.json",
            "crop": {"aspect": "9:16", "width": crop.width, "height": crop.height},
            "solver": {
                "max_speed_px_s": self.max_speed_px_s,
                "max_accel_px_s2": self.max_accel_px_s2,
                "grid_step": 1,
            },
        }


def _fill_rect(
    frame: bytearray,
    width: int,
    height: int,
    left: int,
    top: int,
    rect_width: int,
    rect_height: int,
    color: tuple[int, int, int],
) -> None:
    x0 = max(0, left)
    y0 = max(0, top)
    x1 = min(width, left + rect_width)
    y1 = min(height, top + rect_height)
    if x0 >= x1 or y0 >= y1:
        return
    row = bytes(color) * (x1 - x0)
    for y in range(y0, y1):
        start = (y * width + x0) * 3
        frame[start : start + len(row)] = row


def _draw_rounded_rect(
    frame: bytearray,
    width: int,
    height: int,
    box: Box,
    color: tuple[int, int, int],
    radius: int,
) -> None:
    left = int(round(box.x))
    top = int(round(box.y))
    rect_width = max(1, int(round(box.width)))
    rect_height = max(1, int(round(box.height)))
    radius = max(0, min(radius, rect_width // 2, rect_height // 2))
    for row in range(rect_height):
        if row < radius:
            inset = radius - int(
                round(math.sqrt(max(0, radius * radius - (radius - row) ** 2)))
            )
        elif row >= rect_height - radius:
            distance = row - (rect_height - radius) + 1
            inset = radius - int(
                round(math.sqrt(max(0, radius * radius - distance * distance)))
            )
        else:
            inset = 0
        _fill_rect(
            frame,
            width,
            height,
            left + inset,
            top + row,
            rect_width - 2 * inset,
            1,
            color,
        )


def _background(width: int, height: int) -> bytearray:
    frame = bytearray(width * height * 3)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = (
            int(18 + 20 * ratio),
            int(34 + 42 * ratio),
            int(52 + 44 * ratio),
        )
        row = bytes(color) * width
        start = y * width * 3
        frame[start : start + len(row)] = row
    for x in range(40, width, 120):
        _fill_rect(frame, width, height, x, 0, 3, height, (40, 67, 82))
    _fill_rect(frame, width, height, 0, int(height * 0.82), width, 4, (92, 151, 145))
    _fill_rect(
        frame,
        width,
        height,
        0,
        int(height * 0.82) + 4,
        width,
        height,
        (23, 48, 57),
    )
    return frame


def _draw_subject(
    frame: bytearray,
    width: int,
    height: int,
    box: Box,
    color: tuple[int, int, int],
    index: int,
) -> None:
    shadow = Box(box.x + 7, box.y + 9, box.width, box.height)
    _draw_rounded_rect(frame, width, height, shadow, (12, 24, 30), 24)
    _draw_rounded_rect(frame, width, height, box, color, 24)
    inner = Box(
        box.x + max(5, box.width * 0.12),
        box.y + max(5, box.height * 0.06),
        box.width * 0.76,
        box.height * 0.86,
    )
    _draw_rounded_rect(
        frame,
        width,
        height,
        inner,
        tuple(min(255, channel + 28) for channel in color),
        18,
    )
    stripe_y = int(box.y + box.height * (0.22 if index % 2 == 0 else 0.65))
    _fill_rect(
        frame,
        width,
        height,
        int(box.x + box.width * 0.18),
        stripe_y,
        max(2, int(box.width * 0.64)),
        max(4, int(box.height * 0.035)),
        (242, 238, 220),
    )


def render_frames(scenario: Scenario):
    base = _background(scenario.width, scenario.height)
    for index in range(scenario.frame_count):
        time_s = index / scenario.fps
        frame = bytearray(base)
        for subject_index, region in enumerate(scenario.regions_at(time_s)):
            color = scenario.subjects[
                next(
                    subject_index
                    for subject_index, subject in enumerate(scenario.subjects)
                    if subject.id == region.id
                )
            ].color
            _draw_subject(
                frame,
                scenario.width,
                scenario.height,
                region.box,
                color,
                subject_index,
            )
        progress_width = int((scenario.width - 80) * time_s / scenario.duration_s)
        _fill_rect(
            frame,
            scenario.width,
            scenario.height,
            40,
            scenario.height - 18,
            progress_width,
            5,
            (247, 181, 76),
        )
        yield bytes(frame)


def dual_crossing_scenario(
    width: int = 960,
    height: int = 540,
    fps: int = 30,
) -> Scenario:
    duration = 6.0

    def alice_box(time_s: float) -> Box:
        progress = time_s / duration
        return Box(250 + 350 * progress, 105, 88, 230)

    def bob_box(time_s: float) -> Box:
        progress = time_s / duration
        return Box(618 - 350 * progress, 285, 92, 235)

    return Scenario(
        name="dual_crossing",
        title="Dual-subject crossing",
        width=width,
        height=height,
        fps=fps,
        duration_s=duration,
        subjects=[
            SubjectTrack(
                "alice",
                "person",
                (236, 92, 84),
                alice_box,
                lambda time_s: 0.92 + 0.04 * math.sin(time_s * 1.7),
            ),
            SubjectTrack(
                "bob",
                "person",
                (72, 148, 232),
                bob_box,
                lambda time_s: 0.82 + 0.05 * math.sin(time_s * 2.3 + 0.8),
            ),
        ],
        pins=[
            {
                "frame": int(round(duration * fps / 2)),
                "x": 328,
                "tolerance": 0.0,
                "hard": True,
                "note": "pin the two-person center composition at crossing",
            }
        ],
    )


def side_entry_scenario(
    width: int = 960,
    height: int = 540,
    fps: int = 30,
) -> Scenario:
    duration = 7.0
    entry_time = 2.4

    def anchor_box(time_s: float) -> Box:
        return Box(395 + 8 * math.sin(time_s * 0.9), 165, 90, 270)

    def newcomer_box(time_s: float) -> Box | None:
        if time_s < entry_time:
            return None
        x = max(620.0, 874.0 - 520.0 * (time_s - entry_time))
        return Box(x, 260, 86, 245)

    return Scenario(
        name="side_entry",
        title="Sudden entry from the right edge",
        width=width,
        height=height,
        fps=fps,
        duration_s=duration,
        subjects=[
            SubjectTrack(
                "anchor",
                "person",
                (238, 151, 66),
                anchor_box,
                lambda time_s: 0.93 + 0.03 * math.sin(time_s * 1.1),
            ),
            SubjectTrack(
                "newcomer",
                "person",
                (70, 188, 178),
                newcomer_box,
                lambda time_s: 0.80 + 0.06 * math.sin(time_s * 1.8),
            ),
        ],
        pins=[
            {
                "frame": int(round(1.0 * fps)),
                "x": 328,
                "tolerance": 0.0,
                "hard": True,
                "note": "hold the anchor composition before the entrance",
            }
        ],
        max_speed_px_s=420.0,
        max_accel_px_s2=1400.0,
    )


def get_scenario(
    name: str,
    width: int = 960,
    height: int = 540,
    fps: int = 30,
) -> Scenario:
    if name == "dual_crossing":
        return dual_crossing_scenario(width, height, fps)
    if name == "side_entry":
        return side_entry_scenario(width, height, fps)
    raise ValueError(f"unknown scenario: {name}")


def generate_scenario(
    scenario: Scenario,
    output_dir: str | Path,
    ffmpeg: str,
    crf: int = 23,
) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    source_path = target / "source.mp4"
    count = encode_raw_frames(
        source_path,
        scenario.width,
        scenario.height,
        scenario.fps,
        render_frames(scenario),
        ffmpeg,
        crf=crf,
    )
    write_json(target / "rois.json", scenario.annotation_payload())
    write_json(target / "pins.json", {"pins": scenario.pins})
    write_json(target / "sample.json", scenario.run_config())
    return {
        "scenario": scenario.name,
        "directory": str(target),
        "source_video": str(source_path),
        "annotation": str(target / "rois.json"),
        "pins": str(target / "pins.json"),
        "frame_count": str(count),
    }
