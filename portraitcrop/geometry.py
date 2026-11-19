"""Geometry, coverage scoring, and one-dimensional conflict diagnostics."""

from __future__ import annotations

from .models import Box, CropSpec, FrameAnnotation, Region


def intersection_area(a: Box, b: Box) -> float:
    width = max(0.0, min(a.right, b.right) - max(a.x, b.x))
    height = max(0.0, min(a.bottom, b.bottom) - max(a.y, b.y))
    return width * height


def region_coverage(region: Region, crop_box: Box) -> float:
    area = region.box.width * region.box.height
    if area <= 0:
        return 0.0
    overlap = intersection_area(region.box, crop_box)
    return min(1.0, max(0.0, overlap / area))


def frame_coverage(
    frame: FrameAnnotation,
    crop_box: Box,
    min_confidence: float = 0.0,
) -> tuple[float, dict[str, float]]:
    regions = [region for region in frame.regions if region.confidence >= min_confidence]
    total_weight = sum(region.confidence for region in regions)
    if total_weight <= 0:
        return 1.0, {}
    per_region: dict[str, float] = {}
    weighted = 0.0
    for region in regions:
        value = region_coverage(region, crop_box)
        per_region[region.id] = value
        weighted += region.confidence * value
    return weighted / total_weight, per_region


def active_union(frame: FrameAnnotation, min_confidence: float = 0.0) -> Box | None:
    boxes = [r.box for r in frame.regions if r.confidence >= min_confidence]
    if not boxes:
        return None
    union = boxes[0]
    for box in boxes[1:]:
        union = union.union(box)
    return union


def pin_composition_x(
    frame: FrameAnnotation,
    crop: CropSpec,
    region_ids: tuple[str, ...],
    align: str,
) -> float:
    if not region_ids:
        raise ValueError("region pin requires at least one region id")
    by_id = {region.id: region for region in frame.regions}
    missing = [region_id for region_id in region_ids if region_id not in by_id]
    if missing:
        raise ValueError(
            f"pin references missing region(s) {missing} at frame {frame.index}"
        )
    selected = [by_id[region_id].box for region_id in region_ids]
    union = selected[0]
    for box in selected[1:]:
        union = union.union(box)
    if align == "center":
        target = union.center_x - crop.width / 2.0
    elif align == "left":
        target = union.x
    elif align == "right":
        target = union.right - crop.width
    else:
        raise ValueError(f"unsupported pin alignment: {align!r}")
    return min(max(target, float(crop.x_min)), float(crop.x_max))


def find_conflicts(
    annotations: "AnnotationSet",
    crop: CropSpec,
    min_confidence: float = 0.0,
    max_reports: int | None = None,
) -> list["Conflict"]:
    from .models import AnnotationSet, Conflict

    conflicts: list[Conflict] = []
    for frame in annotations.frames:
        regions = [
            region
            for region in frame.regions
            if region.confidence >= min_confidence
        ]
        for region in regions:
            clipped = region.box.clip(annotations.width, annotations.height)
            if clipped.width > crop.width + 1e-9:
                conflicts.append(
                    Conflict(
                        kind="oversized_region",
                        frame=frame.index,
                        time=frame.time,
                        region_ids=(region.id,),
                        required_span=clipped.width,
                        available_span=float(crop.width),
                        overrun=clipped.width - crop.width,
                        union_box=clipped,
                        confidence=region.confidence,
                    )
                )
        for left_index in range(len(regions)):
            left = regions[left_index]
            left_box = left.box.clip(annotations.width, annotations.height)
            for right in regions[left_index + 1 :]:
                right_box = right.box.clip(annotations.width, annotations.height)
                span = max(left_box.right, right_box.right) - min(
                    left_box.x, right_box.x
                )
                if span <= crop.width + 1e-9:
                    continue
                union = left_box.union(right_box)
                conflicts.append(
                    Conflict(
                        kind="pairwise_span",
                        frame=frame.index,
                        time=frame.time,
                        region_ids=(left.id, right.id),
                        required_span=span,
                        available_span=float(crop.width),
                        overrun=span - crop.width,
                        union_box=union,
                        confidence=left.confidence + right.confidence,
                    )
                )
        if max_reports is not None and len(conflicts) >= max_reports:
            return conflicts[:max_reports]
    return conflicts


def regions_outside_window(
    frame: FrameAnnotation,
    crop_box: Box,
    min_confidence: float = 0.0,
    threshold: float = 0.999,
) -> list[Region]:
    result = []
    for region in frame.regions:
        if region.confidence < min_confidence:
            continue
        if region_coverage(region, crop_box) < threshold:
            result.append(region)
    return result
