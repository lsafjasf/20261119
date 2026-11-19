"""JSON/CSV input and output helpers with permissive annotation parsing."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from .models import (
    AnnotationSet,
    Box,
    Conflict,
    FrameAnnotation,
    Pin,
    Region,
    Window,
)


def load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str | Path, data: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _parse_box(item: dict[str, Any], normalized: bool, width: int, height: int) -> Box:
    bbox = item.get("bbox")
    if bbox is not None:
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise ValueError(f"bbox must have four values: {bbox!r}")
        x, y, box_width, box_height = (float(value) for value in bbox)
    else:
        try:
            x = float(item["x"])
            y = float(item["y"])
            box_width = float(item["w"] if "w" in item else item["width"])
            box_height = float(item["h"] if "h" in item else item["height"])
        except KeyError as error:
            raise ValueError(f"region is missing bbox field: {error}") from error
    if normalized or item.get("normalized", False):
        x, box_width = x * width, box_width * width
        y, box_height = y * height, box_height * height
    if box_width <= 0 or box_height <= 0:
        raise ValueError(f"region has non-positive size: {box_width}x{box_height}")
    return Box(x, y, box_width, box_height)


def _parse_region(
    item: dict[str, Any],
    index: int,
    frame_index: int,
    normalized: bool,
    width: int,
    height: int,
) -> Region:
    region_id = item.get("id", item.get("track_id", item.get("name")))
    if region_id is None:
        region_id = f"r{frame_index}_{index}"
    confidence = float(
        item.get("confidence", item.get("score", item.get("conf", 1.0)))
    )
    confidence = min(1.0, max(0.0, confidence))
    reserved = {
        "id",
        "track_id",
        "name",
        "bbox",
        "x",
        "y",
        "w",
        "width",
        "h",
        "height",
        "confidence",
        "score",
        "conf",
        "label",
        "class",
        "normalized",
    }
    metadata = {key: value for key, value in item.items() if key not in reserved}
    return Region(
        id=str(region_id),
        box=_parse_box(item, normalized, width, height),
        confidence=confidence,
        label=item.get("label", item.get("class")),
        metadata=metadata,
    )


def load_annotations(
    path: str | Path,
    width: int | None = None,
    height: int | None = None,
    fps: float | None = None,
) -> AnnotationSet:
    data = load_json(path)
    if not isinstance(data, dict):
        raise ValueError("annotation JSON must be an object")
    resolved_width = int(width or data.get("width") or 0)
    resolved_height = int(height or data.get("height") or 0)
    resolved_fps = float(fps or data.get("fps") or 0.0)
    if resolved_width <= 0 or resolved_height <= 0:
        raise ValueError("annotation width/height are required")
    if resolved_fps <= 0:
        raise ValueError("annotation fps must be positive or supplied explicitly")
    normalized = bool(data.get("normalized", False))
    raw_frames = data.get("frames", data.get("rois"))
    if raw_frames is None:
        raise ValueError("annotation JSON requires a 'frames' or 'rois' list")

    parsed: dict[int, FrameAnnotation] = {}
    for position, raw_frame in enumerate(raw_frames):
        if isinstance(raw_frame, list):
            frame_index = position
            raw_regions = raw_frame
            raw_time = frame_index / resolved_fps
        elif isinstance(raw_frame, dict):
            frame_index = int(
                raw_frame.get(
                    "frame",
                    raw_frame.get("index", round(float(raw_frame.get("time", 0)) * resolved_fps)),
                )
            )
            raw_time = float(
                raw_frame.get("time", frame_index / resolved_fps)
            )
            raw_regions = raw_frame.get(
                "regions", raw_frame.get("rois", raw_frame.get("detections", []))
            )
        else:
            raise ValueError(f"frame {position} must be an object or region list")
        if frame_index < 0:
            raise ValueError("frame indices must be non-negative")
        if not isinstance(raw_regions, list):
            raise ValueError(f"frame {frame_index} regions must be a list")
        regions = [
            _parse_region(item, index, frame_index, normalized, resolved_width, resolved_height)
            for index, item in enumerate(raw_regions)
        ]
        parsed[frame_index] = FrameAnnotation(frame_index, raw_time, regions)

    if not parsed:
        raise ValueError("annotation file contains no frames")
    last_frame = max(parsed)
    frames = [
        parsed.get(index, FrameAnnotation(index, index / resolved_fps, []))
        for index in range(last_frame + 1)
    ]
    return AnnotationSet(
        width=resolved_width,
        height=resolved_height,
        fps=resolved_fps,
        frames=frames,
        source=str(path),
    )


def _pin_frame(item: dict[str, Any], fps: float, position: int) -> int:
    if "frame" in item:
        return int(item["frame"])
    if "time" in item:
        return int(round(float(item["time"]) * fps))
    return position


def load_pins(path: str | Path | None, fps: float) -> list[Pin]:
    if path is None:
        return []
    data = load_json(path)
    raw_pins = data.get("pins", []) if isinstance(data, dict) else data
    if not isinstance(raw_pins, list):
        raise ValueError("pins JSON must be a list or contain a 'pins' list")
    pins: list[Pin] = []
    for position, item in enumerate(raw_pins):
        if not isinstance(item, dict):
            raise ValueError(f"pin {position} must be an object")
        region_ids = item.get("region_ids", item.get("regions", []))
        if isinstance(region_ids, str):
            region_ids = [region_ids]
        if "region_id" in item:
            region_ids = [item["region_id"]]
        pins.append(
            Pin(
                frame=_pin_frame(item, fps, position),
                x=float(item["x"]) if "x" in item else None,
                region_ids=tuple(str(region_id) for region_id in region_ids),
                tolerance=float(item.get("tolerance", 0.0)),
                hard=bool(item.get("hard", True)),
                align=str(item.get("align", "center")),
                note=str(item.get("note", "")),
            )
        )
    return sorted(pins, key=lambda pin: (pin.frame, pin.x if pin.x is not None else -1))


def write_windows_json(
    path: str | Path,
    windows: Iterable[Window],
    **metadata: Any,
) -> None:
    payload = {"version": 1, **metadata, "windows": [window.to_dict() for window in windows]}
    write_json(path, payload)


def write_windows_csv(path: str | Path, windows: Iterable[Window]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "frame",
        "time",
        "x",
        "y",
        "w",
        "h",
        "coverage",
        "velocity_px_s",
        "acceleration_px_s2",
    ]
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for window in windows:
            writer.writerow(window.to_dict())


def write_conflicts(path: str | Path, conflicts: Iterable[Conflict], **metadata: Any) -> None:
    payload = {
        "version": 1,
        **metadata,
        "conflict_count": len(conflicts) if isinstance(conflicts, list) else None,
        "conflicts": [conflict.to_dict() for conflict in conflicts],
    }
    write_json(path, payload)
