"""Quantitative quality and constraint metrics for a solved path."""

from __future__ import annotations

import math
from typing import Any

from .geometry import frame_coverage
from .models import AnnotationSet, SolveResult
from .solver import SolverConfig


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _rms(values: list[float]) -> float:
    return math.sqrt(_mean([value * value for value in values])) if values else 0.0


def compute_metrics(
    annotations: AnnotationSet,
    result: SolveResult,
    config: SolverConfig,
) -> dict[str, Any]:
    if result.status != "ok" or not result.windows:
        return {
            "status": result.status,
            "reason": result.reason,
            "failed_frame": result.failed_frame,
        }

    fps = annotations.fps
    xs = [window.x for window in result.windows]
    velocities = [0.0]
    for index in range(1, len(xs)):
        velocities.append((xs[index] - xs[index - 1]) * fps)
    accelerations = [0.0]
    for index in range(1, len(xs)):
        acceleration = (
            xs[index] - 2 * xs[index - 1] + xs[index - 2]
            if index >= 2
            else xs[index] - xs[index - 1]
        ) * fps * fps
        accelerations.append(acceleration)

    out_of_bounds: list[int] = []
    for window in result.windows:
        if (
            window.x < 0
            or window.right > annotations.width
            or window.y < 0
            or window.y + window.height > annotations.height
        ):
            out_of_bounds.append(window.frame)
    speed_violations = [
        index
        for index, velocity in enumerate(velocities)
        if abs(velocity) > config.max_speed_px_s + 1e-6
    ]
    acceleration_violations = [
        index
        for index, acceleration in enumerate(accelerations)
        if index >= 1
        and abs(acceleration) > config.max_accel_px_s2 + 1e-6
    ]

    frame_coverages = [window.coverage for window in result.windows]
    subject_values: dict[str, list[float]] = {}
    fully_covered_frames = 0
    for frame, window in zip(annotations.frames, result.windows):
        crop_box = result.crop.box_at(window.x)
        _, per_region = frame_coverage(
            frame, crop_box, config.min_confidence
        )
        for region in frame.regions:
            if region.confidence < config.min_confidence:
                continue
            subject_values.setdefault(region.id, []).append(
                per_region.get(region.id, 0.0)
            )
        if per_region and all(value >= 0.999 for value in per_region.values()):
            fully_covered_frames += 1

    pin_errors: list[dict[str, Any]] = []
    for resolved in result.resolved_pins:
        frame_index = resolved.pin.frame
        if 0 <= frame_index < len(result.windows):
            actual = float(result.windows[frame_index].x)
            pin_errors.append(
                {
                    "frame": frame_index,
                    "hard": resolved.pin.hard,
                    "target_x": round(resolved.target_x, 3),
                    "actual_x": actual,
                    "error_px": round(actual - resolved.target_x, 3),
                    "region_ids": list(resolved.pin.region_ids),
                }
            )

    jitter_values = accelerations[1:]
    jitter_mean = _mean(jitter_values)
    jitter_variance = _mean(
        [(value - jitter_mean) ** 2 for value in jitter_values]
    )
    return {
        "status": "ok",
        "frame_count": len(result.windows),
        "duration_s": round(len(result.windows) / fps, 6),
        "crop": {
            "x_range": [min(xs), max(xs)],
            "width": result.crop.width,
            "height": result.crop.height,
            "aspect_ratio": round(result.crop.aspect_ratio, 6),
        },
        "coverage": {
            "mean_weighted": round(_mean(frame_coverages), 6),
            "min_weighted": round(min(frame_coverages), 6),
            "full_frame_rate": round(
                fully_covered_frames / len(result.windows), 6
            ),
            "by_subject": {
                region_id: {
                    "mean": round(_mean(values), 6),
                    "min": round(min(values), 6),
                    "frames_present": len(values),
                }
                for region_id, values in sorted(subject_values.items())
            },
        },
        "motion": {
            "max_speed_px_s": round(max(abs(value) for value in velocities), 4),
            "max_acceleration_px_s2": round(
                max(abs(value) for value in accelerations), 4
            ),
            "jitter_rms_px_s2": round(_rms(jitter_values), 4),
            "jitter_std_px_s2": round(math.sqrt(jitter_variance), 4),
            "mean_abs_acceleration_px_s2": round(
                _mean([abs(value) for value in jitter_values]), 4
            ),
            "direction_reversals": sum(
                1
                for index in range(2, len(velocities))
                if velocities[index] * velocities[index - 1] < 0
            ),
        },
        "constraints": {
            "max_speed_limit_px_s": config.max_speed_px_s,
            "max_acceleration_limit_px_s2": config.max_accel_px_s2,
            "speed_violation_frames": len(speed_violations),
            "acceleration_violation_frames": len(acceleration_violations),
            "speed_violating_frame_indices": speed_violations[:20],
            "acceleration_violating_frame_indices": acceleration_violations[:20],
            "out_of_bounds_frames": len(out_of_bounds),
            "out_of_bounds_frame_indices": out_of_bounds[:20],
        },
        "pins": pin_errors,
    }
