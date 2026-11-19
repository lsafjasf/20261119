"""Resolve manual framing pins into crop-left coordinate targets."""

from __future__ import annotations

from .geometry import pin_composition_x
from .models import AnnotationSet, CropSpec, Pin, ResolvedPin


def resolve_pins(
    annotations: AnnotationSet,
    crop: CropSpec,
    pins: list[Pin],
) -> tuple[list[ResolvedPin], list[str]]:
    warnings: list[str] = []
    resolved: list[ResolvedPin] = []
    frame_count = len(annotations.frames)
    for pin in pins:
        if pin.frame < 0 or pin.frame >= frame_count:
            warnings.append(
                f"pin frame {pin.frame} is outside the {frame_count}-frame annotation"
            )
            continue
        if pin.x is None and not pin.region_ids:
            raise ValueError(f"pin at frame {pin.frame} needs x or region_id")
        frame = annotations.frames[pin.frame]
        if pin.region_ids:
            target = pin_composition_x(frame, crop, pin.region_ids, pin.align)
        else:
            target = float(pin.x)  # type: ignore[arg-type]
            if target < crop.x_min or target > crop.x_max:
                warnings.append(
                    f"pin at frame {pin.frame} x={target:g} clamped to "
                    f"[{crop.x_min}, {crop.x_max}]"
                )
                target = min(max(target, float(crop.x_min)), float(crop.x_max))
        if pin.tolerance < 0:
            raise ValueError(f"pin at frame {pin.frame} has negative tolerance")
        resolved.append(ResolvedPin(pin=pin, target_x=target))
    return resolved, warnings
