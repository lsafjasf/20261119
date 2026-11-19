"""Second-order dynamic-programming solver for crop-window paths."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from .geometry import frame_coverage
from .models import (
    AnnotationSet,
    CropSpec,
    Pin,
    ResolvedPin,
    SolveResult,
    Window,
)
from .pins import resolve_pins


@dataclass
class SolverConfig:
    max_speed_px_s: float = 420.0
    max_accel_px_s2: float = 1400.0
    grid_step: int = 1
    coverage_weight: float = 20.0
    centering_weight: float = 3.0
    velocity_weight: float = 0.35
    acceleration_weight: float = 4.0
    pin_weight: float = 50.0
    min_confidence: float = 0.0
    max_conflicts_in_failure: int = 5
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_speed_px_s < 0 or self.max_accel_px_s2 < 0:
            raise ValueError("speed and acceleration limits must be non-negative")
        if self.grid_step < 1:
            raise ValueError("grid_step must be at least 1")


def _candidate_positions(crop: CropSpec, grid_step: int) -> list[int]:
    positions = list(range(crop.x_min, crop.x_max + 1, grid_step))
    if not positions or positions[-1] != crop.x_max:
        positions.append(crop.x_max)
    return positions


def _hard_targets(resolved: list[ResolvedPin], frame: int) -> list[ResolvedPin]:
    return [item for item in resolved if item.pin.frame == frame and item.pin.hard]


def _soft_targets(resolved: list[ResolvedPin], frame: int) -> list[ResolvedPin]:
    return [item for item in resolved if item.pin.frame == frame and not item.pin.hard]


def _target_is_accepted(x: int, targets: list[ResolvedPin]) -> bool:
    for item in targets:
        tolerance = max(float(item.pin.tolerance), 0.5)
        if abs(float(x) - item.target_x) > tolerance + 1e-9:
            return False
    return True


def _emission(
    annotations: AnnotationSet,
    crop: CropSpec,
    x_values: list[int],
    resolved: list[ResolvedPin],
    config: SolverConfig,
) -> tuple[list[list[float]], list[list[float]]]:
    costs: list[list[float]] = []
    coverages: list[list[float]] = []
    for frame in annotations.frames:
        frame_costs: list[float] = []
        frame_coverages: list[float] = []
        for x in x_values:
            crop_box = crop.box_at(x)
            coverage, per_region = frame_coverage(
                frame, crop_box, config.min_confidence
            )
            weighted_center_error = 0.0
            total_weight = 0.0
            for region in frame.regions:
                if region.confidence < config.min_confidence:
                    continue
                region_cover = per_region.get(region.id, 0.0)
                half_width = max(crop.width / 2.0, 1.0)
                error = min(
                    1.0, abs(region.box.center_x - crop_box.center_x) / half_width
                )
                weighted_center_error += (
                    region.confidence * region_cover * error
                )
                total_weight += region.confidence
            center_error = (
                weighted_center_error / total_weight if total_weight > 0 else 0.0
            )
            cost = (
                -config.coverage_weight * coverage
                + config.centering_weight * center_error
            )
            for item in _soft_targets(resolved, frame.index):
                scale = max(float(item.pin.tolerance), 1.0)
                cost += config.pin_weight * ((x - item.target_x) / scale) ** 2
            frame_costs.append(cost)
            frame_coverages.append(coverage)
        costs.append(frame_costs)
        coverages.append(frame_coverages)
    return costs, coverages


def _infeasible(
    crop: CropSpec,
    annotations: AnnotationSet,
    resolved: list[ResolvedPin],
    warnings: list[str],
    failed_frame: int,
    reason: str,
) -> SolveResult:
    return SolveResult(
        status="infeasible",
        crop=crop,
        fps=annotations.fps,
        windows=[],
        failed_frame=failed_frame,
        reason=reason,
        warnings=warnings,
        pins=[item.pin for item in resolved],
        resolved_pins=resolved,
    )


def solve_path(
    annotations: AnnotationSet,
    crop: CropSpec,
    config: SolverConfig | None = None,
    pins: Iterable[Pin] | None = None,
) -> SolveResult:
    """Find the minimum-cost path with bounded speed and acceleration."""
    config = config or SolverConfig()
    resolved, pin_warnings = resolve_pins(
        annotations, crop, list(pins or [])
    )
    warnings = [*config.warnings, *pin_warnings]
    if not annotations.frames:
        raise ValueError("cannot solve an empty annotation set")
    if abs(annotations.width - crop.source_width) > 0:
        raise ValueError("crop source width does not match annotations")
    if abs(annotations.height - crop.source_height) > 0:
        raise ValueError("crop source height does not match annotations")

    fps = annotations.fps
    dt = 1.0 / fps
    max_velocity = config.max_speed_px_s * dt
    max_acceleration = config.max_accel_px_s2 * dt * dt
    velocity_limit = int(math.floor(max_velocity + 1e-9))
    if max_velocity < 1.0:
        warnings.append(
            "integer-pixel path has less than 1 px/frame speed headroom; "
            "panning may be limited by coordinate quantization"
        )
    if max_acceleration < 1.0:
        warnings.append(
            "integer-pixel path has less than 1 px/frame^2 acceleration "
            "headroom; velocity changes may be blocked by quantization"
        )

    x_values = _candidate_positions(crop, config.grid_step)
    index_by_x = {x: index for index, x in enumerate(x_values)}
    hard_by_frame = {
        frame.index: _hard_targets(resolved, frame.index)
        for frame in annotations.frames
    }
    valid_by_frame: list[list[bool]] = []
    for frame in annotations.frames:
        hard = hard_by_frame[frame.index]
        valid = [_target_is_accepted(x, hard) for x in x_values]
        if hard and not any(valid):
            targets = ", ".join(f"{item.target_x:.1f}" for item in hard)
            return _infeasible(
                crop,
                annotations,
                resolved,
                warnings,
                frame.index,
                f"hard pin target(s) {targets} cannot be represented at frame "
                f"{frame.index}",
            )
        valid_by_frame.append(valid)

    emission, coverage_values = _emission(
        annotations, crop, x_values, resolved, config
    )
    acceleration_normalizer = max(max_acceleration, 1e-9)
    velocity_normalizer = max(float(velocity_limit), 1e-9)

    dp: dict[tuple[int, int], float] = {}
    for index, x in enumerate(x_values):
        if not valid_by_frame[0][index]:
            continue
        dp[(index, 0)] = emission[0][index]
    if not dp:
        return _infeasible(
            crop,
            annotations,
            resolved,
            warnings,
            0,
            "no crop position is reachable at frame 0",
        )

    back: list[dict[tuple[int, int], tuple[int, int]]] = [{}]
    for frame_index in range(1, len(annotations.frames)):
        next_dp: dict[tuple[int, int], float] = {}
        next_back: dict[tuple[int, int], tuple[int, int]] = {}
        valid = valid_by_frame[frame_index]
        frame_cost = emission[frame_index]
        for (previous_index, previous_velocity), path_cost in dp.items():
            previous_x = x_values[previous_index]
            low = max(
                -velocity_limit,
                int(math.ceil(previous_velocity - max_acceleration - 1e-9)),
            )
            high = min(
                velocity_limit,
                int(math.floor(previous_velocity + max_acceleration + 1e-9)),
            )
            for velocity in range(low, high + 1):
                current_x = previous_x + velocity
                current_index = index_by_x.get(current_x)
                if current_index is None or not valid[current_index]:
                    continue
                acceleration = velocity - previous_velocity
                cost = (
                    path_cost
                    + frame_cost[current_index]
                    + config.acceleration_weight
                    * (acceleration / acceleration_normalizer) ** 2
                    + config.velocity_weight
                    * (velocity / velocity_normalizer) ** 2
                )
                key = (current_index, velocity)
                if cost < next_dp.get(key, math.inf):
                    next_dp[key] = cost
                    next_back[key] = (previous_index, previous_velocity)
        if not next_dp:
            return _infeasible(
                crop,
                annotations,
                resolved,
                warnings,
                frame_index,
                "no path can satisfy speed, acceleration, and hard pins at "
                f"frame {frame_index}",
            )
        dp = next_dp
        back.append(next_back)

    center_position = crop.x_max / 2.0
    best_key = min(
        dp,
        key=lambda key: (
            dp[key],
            abs(x_values[key[0]] - center_position),
            key[1],
        ),
    )
    indices: list[int] = [0] * len(annotations.frames)
    keys: list[tuple[int, int] | None] = [None] * len(annotations.frames)
    keys[-1] = best_key
    for frame_index in range(len(annotations.frames) - 1, 0, -1):
        key = keys[frame_index]
        assert key is not None
        indices[frame_index] = key[0]
        keys[frame_index - 1] = back[frame_index][key]
    assert keys[0] is not None
    indices[0] = keys[0][0]

    windows: list[Window] = []
    previous_x: int | None = None
    previous_velocity = 0.0
    for frame_index, (index, key) in enumerate(zip(indices, keys)):
        x = x_values[index]
        velocity = 0.0 if previous_x is None else (x - previous_x) * fps
        acceleration = (
            0.0
            if previous_x is None
            else (velocity - previous_velocity) * fps
        )
        windows.append(
            Window(
                frame=frame_index,
                time=frame_index / fps,
                x=x,
                y=crop.y,
                width=crop.width,
                height=crop.height,
                coverage=coverage_values[frame_index][index],
                velocity_px_s=velocity,
                acceleration_px_s2=acceleration,
            )
        )
        previous_x = x
        previous_velocity = velocity
    return SolveResult(
        status="ok",
        crop=crop,
        fps=fps,
        windows=windows,
        total_cost=dp[best_key],
        warnings=warnings,
        pins=[item.pin for item in resolved],
        resolved_pins=resolved,
    )
