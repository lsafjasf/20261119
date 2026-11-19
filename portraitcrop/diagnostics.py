"""Conflict reporting for regions that cannot share one crop window."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .geometry import find_conflicts, frame_coverage
from .models import AnnotationSet, Conflict, CropSpec, SolveResult


def pin_exclusion_conflicts(
    annotations: AnnotationSet,
    crop: CropSpec,
    result: SolveResult,
    min_confidence: float = 0.0,
) -> list[Conflict]:
    """Report regions a manual pin deliberately leaves outside the window."""
    conflicts: list[Conflict] = []
    for resolved in result.resolved_pins:
        frame_index = resolved.pin.frame
        if frame_index < 0 or frame_index >= len(annotations.frames):
            continue
        frame = annotations.frames[frame_index]
        x = resolved.target_x
        if result.windows:
            x = float(result.windows[frame_index].x)
        crop_box = crop.box_at(x)
        _, per_region = frame_coverage(frame, crop_box, min_confidence)
        for region in frame.regions:
            if region.confidence < min_confidence:
                continue
            coverage = per_region.get(region.id, 0.0)
            if coverage >= 0.999:
                continue
            clipped = region.box.clip(annotations.width, annotations.height)
            conflicts.append(
                Conflict(
                    kind="pin_excludes_region",
                    frame=frame_index,
                    time=frame.time,
                    region_ids=(region.id,),
                    required_span=clipped.width,
                    available_span=clipped.width * coverage,
                    overrun=clipped.width * (1.0 - coverage),
                    union_box=clipped,
                    confidence=region.confidence,
                )
            )
    return conflicts


def diagnose(
    annotations: AnnotationSet,
    crop: CropSpec,
    result: SolveResult,
    min_confidence: float = 0.0,
) -> list[Conflict]:
    conflicts = find_conflicts(
        annotations, crop, min_confidence=min_confidence
    )
    conflicts.extend(
        pin_exclusion_conflicts(
            annotations, crop, result, min_confidence=min_confidence
        )
    )
    return conflicts


def summarize_conflicts(conflicts: list[Conflict]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, tuple[str, ...]], list[Conflict]] = defaultdict(list)
    for conflict in conflicts:
        groups[(conflict.kind, conflict.region_ids)].append(conflict)
    summary: list[dict[str, Any]] = []
    for (kind, region_ids), items in groups.items():
        worst = max(items, key=lambda conflict: conflict.overrun)
        summary.append(
            {
                "kind": kind,
                "region_ids": list(region_ids),
                "frame_count": len(items),
                "first_frame": min(item.frame for item in items),
                "last_frame": max(item.frame for item in items),
                "max_overrun_px": round(worst.overrun, 3),
                "worst_frame": worst.frame,
                "worst_required_span": round(worst.required_span, 3),
                "available_span": round(worst.available_span, 3),
            }
        )
    return sorted(
        summary,
        key=lambda item: (
            -item["max_overrun_px"],
            item["region_ids"],
            item["kind"],
        ),
    )
