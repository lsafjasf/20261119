"""Versioned JSON edit-list serialization.

Anchors and marker ids are the source of truth.  Derived media/timeline times
are exported for edit-list consumers but are deliberately ignored on import.
That makes repeated export/import round trips lossless.
"""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping

from .model import (
    Anchor,
    Clip,
    MarkerError,
    OwnerKind,
    Timeline,
)
from .timecode import TICKS_PER_SECOND

EDIT_LIST_FORMAT = "timeline-marker-edit-list"
EDIT_LIST_VERSION = 1


def _speed_document(speed: Fraction) -> dict[str, int]:
    return {
        "numerator": speed.numerator,
        "denominator": speed.denominator,
    }


def _owner_document(owner_kind: OwnerKind, owner_id: str) -> dict[str, str]:
    return {"kind": owner_kind.value, "id": owner_id}


def export_edit_list(timeline: Timeline) -> dict[str, Any]:
    """Return a deterministic, JSON-safe edit list for ``timeline``.

    Every stored marker appears exactly once in ``markers`` with its resolved
    media time (when it belongs to a clip) and resolved timeline position.
    """

    if not isinstance(timeline, Timeline):
        raise TypeError("timeline must be a Timeline instance")

    clips = sorted(
        timeline.clips, key=lambda clip: (clip.timeline_start, clip.id)
    )
    clip_documents = [
        {
            "id": clip.id,
            "asset_id": clip.asset_id,
            "timeline_start": clip.timeline_start,
            "source_in": clip.source_in,
            "source_out": clip.source_out,
            "speed": _speed_document(clip.speed),
            "timeline_duration": clip.timeline_duration,
            "timeline_end": clip.timeline_end,
        }
        for clip in clips
    ]

    marker_documents = [
        {
            "id": occurrence.id,
            "owner": _owner_document(occurrence.owner_kind, occurrence.owner_id),
            "anchor": occurrence.anchor.value,
            "anchor_tick": occurrence.anchor_tick,
            "color": occurrence.color,
            "note": occurrence.note,
            "event_type": occurrence.event_type,
            "media_tick": occurrence.media_tick,
            "timeline_tick": occurrence.timeline_tick,
            "visible": occurrence.visible,
        }
        for occurrence in timeline.occurrences(include_hidden=True)
    ]

    return {
        "format": EDIT_LIST_FORMAT,
        "version": EDIT_LIST_VERSION,
        "ticks_per_second": TICKS_PER_SECOND,
        "duration_tick": timeline.duration_tick,
        "clips": clip_documents,
        "markers": marker_documents,
    }


def dumps_edit_list(timeline: Timeline) -> str:
    """Serialize with stable key order and no floating-point numbers."""

    return (
        json.dumps(
            export_edit_list(timeline),
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        )
        + "\n"
    )


def write_edit_list(timeline: Timeline, path: str | Path) -> Path:
    destination = Path(path)
    destination.write_text(dumps_edit_list(timeline), encoding="utf-8")
    return destination


def _require_mapping(value: object, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MarkerError(f"{where} must be a JSON object")
    return value


def _require_integer(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MarkerError(f"{where} must be an integer tick")
    return value


def _read_anchor(value: object, where: str) -> Anchor:
    try:
        return Anchor(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise MarkerError(f"{where} has an invalid anchor") from exc


def _require_text(value: object, where: str, *, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str) or not value:
        raise MarkerError(f"{where} must be a non-empty string")
    return value


def _read_speed(value: object, where: str) -> Fraction:
    speed = _require_mapping(value, where)
    numerator = _require_integer(speed.get("numerator"), f"{where}.numerator")
    denominator = _require_integer(speed.get("denominator"), f"{where}.denominator")
    if numerator <= 0 or denominator <= 0:
        raise MarkerError(f"{where} must contain positive integers")
    return Fraction(numerator, denominator)


def load_edit_list(document: Mapping[str, Any] | str | bytes) -> Timeline:
    """Read an edit list, preserving ids and anchor values exactly."""

    if isinstance(document, (str, bytes)):
        try:
            decoded = json.loads(document)
        except json.JSONDecodeError as exc:
            raise MarkerError(f"invalid edit-list JSON: {exc.msg}") from exc
    else:
        decoded = document
    root = _require_mapping(decoded, "edit list")
    if root.get("format") != EDIT_LIST_FORMAT:
        raise MarkerError(f"unsupported edit-list format: {root.get('format')!r}")
    if root.get("version") != EDIT_LIST_VERSION:
        raise MarkerError(f"unsupported edit-list version: {root.get('version')!r}")

    ticks_per_second = _require_integer(root.get("ticks_per_second"), "ticks_per_second")
    if ticks_per_second != TICKS_PER_SECOND:
        raise MarkerError(
            f"this implementation requires ticks_per_second={TICKS_PER_SECOND}"
        )

    clips_value = root.get("clips")
    markers_value = root.get("markers")
    if not isinstance(clips_value, list):
        raise MarkerError("clips must be a JSON array")
    if not isinstance(markers_value, list):
        raise MarkerError("markers must be a JSON array")

    timeline = Timeline()
    for index, value in enumerate(clips_value):
        where = f"clips[{index}]"
        clip_document = _require_mapping(value, where)
        clip = Clip(
            id=_require_text(clip_document.get("id"), f"{where}.id"),
            asset_id=_require_text(clip_document.get("asset_id"), f"{where}.asset_id"),
            timeline_start=_require_integer(
                clip_document.get("timeline_start"), f"{where}.timeline_start"
            ),
            source_in=_require_integer(
                clip_document.get("source_in"), f"{where}.source_in"
            ),
            source_out=_require_integer(
                clip_document.get("source_out"), f"{where}.source_out"
            ),
            speed=_read_speed(clip_document.get("speed"), f"{where}.speed"),
        )
        timeline.attach_clip(clip)

    for index, value in enumerate(markers_value):
        where = f"markers[{index}]"
        marker_document = _require_mapping(value, where)
        marker_id = _require_text(marker_document.get("id"), f"{where}.id")
        owner = _require_mapping(marker_document.get("owner"), f"{where}.owner")
        owner_kind_value = owner.get("kind")
        owner_id = _require_text(owner.get("id"), f"{where}.owner.id")
        anchor = _read_anchor(marker_document.get("anchor"), f"{where}.anchor")
        anchor_tick = _require_integer(
            marker_document.get("anchor_tick"), f"{where}.anchor_tick"
        )
        color = _require_text(marker_document.get("color"), f"{where}.color")
        event_type = _require_text(
            marker_document.get("event_type"), f"{where}.event_type"
        )
        note = marker_document.get("note", "")
        if not isinstance(note, str):
            raise MarkerError(f"{where}.note must be a string")

        if owner_kind_value == OwnerKind.CLIP.value:
            timeline.add_clip_marker(
                owner_id,
                anchor,
                anchor_tick,
                color=color,
                note=note,
                event_type=event_type,
                marker_id=marker_id,
            )
        elif owner_kind_value == OwnerKind.TIMELINE.value:
            if owner_id != "timeline":
                raise MarkerError(f"{where}.owner.id must be 'timeline'")
            if anchor is not Anchor.GLOBAL:
                raise MarkerError("timeline markers must use a global anchor")
            timeline.add_timeline_marker(
                anchor_tick,
                color=color,
                note=note,
                event_type=event_type,
                marker_id=marker_id,
            )
        else:
            raise MarkerError(f"{where}.owner.kind is invalid")

    return timeline


def read_edit_list(path: str | Path) -> Timeline:
    return load_edit_list(Path(path).read_text(encoding="utf-8"))


def loads_edit_list(text: str | bytes) -> Timeline:
    return load_edit_list(text)
