"""Clips, markers, anchor semantics, and edit operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction
from uuid import uuid4

from .timecode import Number, as_fraction, round_fraction


class MarkerError(ValueError):
    """Raised when marker or clip data is not internally consistent."""


class Anchor(str, Enum):
    """How a marker's stored tick is interpreted."""

    MEDIA = "media"
    CLIP = "clip"
    GLOBAL = "global"


class OwnerKind(str, Enum):
    CLIP = "clip"
    TIMELINE = "timeline"


def _coerce_anchor(value: Anchor | str) -> Anchor:
    try:
        return value if isinstance(value, Anchor) else Anchor(value)
    except ValueError as exc:
        allowed = ", ".join(anchor.value for anchor in Anchor)
        raise MarkerError(f"anchor must be one of: {allowed}") from exc


def _require_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MarkerError(f"{name} must be a non-empty string")
    return value


def _require_tick(name: str, value: object, *, allow_zero: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MarkerError(f"{name} must be an integer tick")
    if value < 0 or (not allow_zero and value == 0):
        raise MarkerError(f"{name} must be {'positive' if not allow_zero else 'non-negative'}")
    return value


@dataclass(frozen=True, slots=True)
class Marker:
    """A marker with an immutable id and an immutable anchor value.

    ``anchor_tick`` is a media tick for :attr:`Anchor.MEDIA`, an offset from
    the clip's timeline start for :attr:`Anchor.CLIP`, and an absolute
    timeline tick for :attr:`Anchor.GLOBAL`.
    """

    id: str
    anchor: Anchor | str
    anchor_tick: int
    color: str = "yellow"
    note: str = ""
    event_type: str = "comment"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require_text("marker id", self.id))
        object.__setattr__(self, "anchor", _coerce_anchor(self.anchor))
        object.__setattr__(
            self, "anchor_tick", _require_tick("anchor_tick", self.anchor_tick)
        )
        object.__setattr__(self, "color", _require_text("color", self.color))
        object.__setattr__(self, "event_type", _require_text("event_type", self.event_type))
        if not isinstance(self.note, str):
            raise MarkerError("note must be a string")

    @property
    def comment(self) -> str:
        """Alias for ``note`` for callers using comment terminology."""

        return self.note

    def with_metadata(
        self,
        *,
        color: str | None = None,
        note: str | None = None,
        event_type: str | None = None,
    ) -> Marker:
        return Marker(
            id=self.id,
            anchor=self.anchor,
            anchor_tick=self.anchor_tick,
            color=self.color if color is None else color,
            note=self.note if note is None else note,
            event_type=self.event_type if event_type is None else event_type,
        )


@dataclass(slots=True)
class Clip:
    """A timeline clip and its private marker collection."""

    id: str
    asset_id: str
    timeline_start: int
    source_in: int
    source_out: int
    speed: Number = 1
    markers: dict[str, Marker] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = _require_text("clip id", self.id)
        self.asset_id = _require_text("asset_id", self.asset_id)
        self.timeline_start = _require_tick("timeline_start", self.timeline_start)
        self.source_in = _require_tick("source_in", self.source_in)
        self.source_out = _require_tick("source_out", self.source_out)
        if self.source_out <= self.source_in:
            raise MarkerError("source_out must be greater than source_in")
        try:
            speed = as_fraction(self.speed)
        except (TypeError, ValueError) as exc:
            raise MarkerError(str(exc)) from exc
        if speed <= 0:
            raise MarkerError("speed must be positive")
        self.speed = speed

    @property
    def source_duration(self) -> int:
        return self.source_out - self.source_in

    @property
    def timeline_duration(self) -> int:
        duration = round_fraction(Fraction(self.source_duration, 1) / self.speed)
        return max(1, duration)

    @property
    def timeline_end(self) -> int:
        return self.timeline_start + self.timeline_duration

    def contains_source(self, source_tick: int) -> bool:
        return self.source_in <= source_tick < self.source_out

    def contains_timeline(self, timeline_tick: int) -> bool:
        return self.timeline_start <= timeline_tick < self.timeline_end

    def source_to_timeline_tick(self, source_tick: int) -> int:
        offset = Fraction(source_tick - self.source_in, 1) / self.speed
        return self.timeline_start + round_fraction(offset)

    def timeline_to_source_tick(self, timeline_tick: int) -> int:
        offset = Fraction(timeline_tick - self.timeline_start, 1) * self.speed
        return self.source_in + round_fraction(offset)

    def marker_timeline_tick(self, marker: Marker) -> int:
        if marker.anchor is Anchor.MEDIA:
            return self.source_to_timeline_tick(marker.anchor_tick)
        if marker.anchor is Anchor.CLIP:
            return self.timeline_start + marker.anchor_tick
        raise MarkerError("global markers do not belong to a clip")

    def marker_media_tick(self, marker: Marker) -> int:
        if marker.anchor is Anchor.MEDIA:
            return marker.anchor_tick
        if marker.anchor is Anchor.CLIP:
            return self.timeline_to_source_tick(self.timeline_start + marker.anchor_tick)
        raise MarkerError("global markers have no media time")

    def marker_visible(self, marker: Marker) -> bool:
        if marker.anchor is Anchor.MEDIA:
            return self.contains_source(marker.anchor_tick)
        if marker.anchor is Anchor.CLIP:
            return self.contains_timeline(self.timeline_start + marker.anchor_tick)
        raise MarkerError("global markers do not belong to a clip")


@dataclass(frozen=True, slots=True)
class MarkerOccurrence:
    """A marker resolved against the current edit, including owner and time."""

    id: str
    owner_kind: OwnerKind
    owner_id: str
    anchor: Anchor
    anchor_tick: int
    color: str
    note: str
    event_type: str
    timeline_tick: int | None
    media_tick: int | None
    visible: bool

    @property
    def comment(self) -> str:
        return self.note


class Timeline:
    """A small edit timeline with clip-private and timeline/global markers.

    Clip markers are owned by exactly one clip and are removed with it.
    Timeline markers are global and are unaffected by clip edits.
    """

    def __init__(self) -> None:
        self._clips: dict[str, Clip] = {}
        self._timeline_markers: dict[str, Marker] = {}

    @property
    def clips(self) -> tuple[Clip, ...]:
        return tuple(self._clips.values())

    @property
    def timeline_markers(self) -> tuple[Marker, ...]:
        return tuple(self._timeline_markers.values())

    @property
    def duration_tick(self) -> int:
        return max((clip.timeline_end for clip in self._clips.values()), default=0)

    def __len__(self) -> int:
        return len(self._clips)

    def __contains__(self, clip_id: object) -> bool:
        return clip_id in self._clips

    def clip(self, clip_id: str) -> Clip:
        try:
            return self._clips[clip_id]
        except KeyError as exc:
            raise MarkerError(f"unknown clip: {clip_id}") from exc

    def add_clip(
        self,
        asset_id: str,
        *,
        timeline_start: int,
        source_in: int,
        source_out: int,
        speed: Number = 1,
        clip_id: str | None = None,
    ) -> Clip:
        chosen_id = clip_id or f"clip-{uuid4().hex}"
        if chosen_id in self._clips:
            raise MarkerError(f"duplicate clip id: {chosen_id}")
        clip = Clip(
            id=chosen_id,
            asset_id=asset_id,
            timeline_start=timeline_start,
            source_in=source_in,
            source_out=source_out,
            speed=speed,
        )
        self._clips[clip.id] = clip
        return clip

    def attach_clip(self, clip: Clip) -> Clip:
        """Attach an existing clip, used by the edit-list reader."""

        if not isinstance(clip, Clip):
            raise TypeError("clip must be a Clip instance")
        if clip.id in self._clips:
            raise MarkerError(f"duplicate clip id: {clip.id}")
        for key, marker in clip.markers.items():
            if key != marker.id:
                raise MarkerError("clip marker dictionary key must equal marker id")
            if marker.anchor is Anchor.GLOBAL:
                raise MarkerError("global anchors cannot belong to a clip")
            self._ensure_marker_id_available(marker.id)
        self._clips[clip.id] = clip
        return clip

    def remove_clip(self, clip_id: str) -> tuple[Marker, ...]:
        """Delete a clip and cascade-delete all of its private markers."""

        clip = self.clip(clip_id)
        removed_markers = tuple(clip.markers.values())
        del self._clips[clip_id]
        return removed_markers

    def move_clip(self, clip_id: str, new_timeline_start: int) -> Clip:
        clip = self.clip(clip_id)
        clip.timeline_start = _require_tick("new_timeline_start", new_timeline_start)
        return clip

    def trim_clip(
        self,
        clip_id: str,
        *,
        source_in: int | None = None,
        source_out: int | None = None,
    ) -> Clip:
        """Change source bounds without moving the visible clip's start."""

        clip = self.clip(clip_id)
        new_in = clip.source_in if source_in is None else _require_tick("source_in", source_in)
        new_out = clip.source_out if source_out is None else _require_tick("source_out", source_out)
        if new_out <= new_in:
            raise MarkerError("source_out must be greater than source_in")
        clip.source_in = new_in
        clip.source_out = new_out
        return clip

    def set_speed(self, clip_id: str, speed: Number) -> Clip:
        clip = self.clip(clip_id)
        candidate = Clip(
            id=clip.id,
            asset_id=clip.asset_id,
            timeline_start=clip.timeline_start,
            source_in=clip.source_in,
            source_out=clip.source_out,
            speed=speed,
            markers=clip.markers,
        )
        clip.speed = candidate.speed
        return clip

    retime_clip = set_speed

    def add_clip_marker(
        self,
        clip_id: str,
        anchor: Anchor | str,
        anchor_tick: int,
        *,
        color: str = "yellow",
        note: str = "",
        event_type: str = "comment",
        marker_id: str | None = None,
    ) -> Marker:
        clip = self.clip(clip_id)
        resolved_anchor = _coerce_anchor(anchor)
        if resolved_anchor is Anchor.GLOBAL:
            raise MarkerError("global markers belong on the timeline, not a clip")
        chosen_id = marker_id or f"marker-{uuid4().hex}"
        self._ensure_marker_id_available(chosen_id)
        marker = Marker(
            id=chosen_id,
            anchor=resolved_anchor,
            anchor_tick=_require_tick("anchor_tick", anchor_tick),
            color=color,
            note=note,
            event_type=event_type,
        )
        clip.markers[marker.id] = marker
        return marker

    def add_media_marker(
        self, clip_id: str, source_tick: int, **metadata: object
    ) -> Marker:
        return self.add_clip_marker(
            clip_id, Anchor.MEDIA, source_tick, **metadata  # type: ignore[arg-type]
        )

    def add_clip_anchor_marker(
        self, clip_id: str, offset_tick: int, **metadata: object
    ) -> Marker:
        return self.add_clip_marker(
            clip_id, Anchor.CLIP, offset_tick, **metadata  # type: ignore[arg-type]
        )

    def add_timeline_marker(
        self,
        timeline_tick: int,
        *,
        color: str = "yellow",
        note: str = "",
        event_type: str = "comment",
        marker_id: str | None = None,
    ) -> Marker:
        chosen_id = marker_id or f"marker-{uuid4().hex}"
        self._ensure_marker_id_available(chosen_id)
        marker = Marker(
            id=chosen_id,
            anchor=Anchor.GLOBAL,
            anchor_tick=_require_tick("timeline_tick", timeline_tick),
            color=color,
            note=note,
            event_type=event_type,
        )
        self._timeline_markers[marker.id] = marker
        return marker

    add_global_marker = add_timeline_marker

    def markers(self) -> tuple[Marker, ...]:
        clip_markers = (
            marker for clip in self._clips.values() for marker in clip.markers.values()
        )
        return tuple(clip_markers) + tuple(self._timeline_markers.values())

    def _find_marker(self, marker_id: str) -> tuple[OwnerKind, str, Marker] | None:
        for clip in self._clips.values():
            marker = clip.markers.get(marker_id)
            if marker is not None:
                return OwnerKind.CLIP, clip.id, marker
        marker = self._timeline_markers.get(marker_id)
        if marker is not None:
            return OwnerKind.TIMELINE, "timeline", marker
        return None

    def _ensure_marker_id_available(self, marker_id: str) -> None:
        if self._find_marker(marker_id) is not None:
            raise MarkerError(f"duplicate marker id: {marker_id}")

    def marker(self, marker_id: str) -> Marker:
        found = self._find_marker(marker_id)
        if found is None:
            raise MarkerError(f"unknown marker: {marker_id}")
        return found[2]

    def update_marker(
        self,
        marker_id: str,
        *,
        color: str | None = None,
        note: str | None = None,
        event_type: str | None = None,
    ) -> Marker:
        """Update presentation metadata without changing the stable id or anchor."""

        found = self._find_marker(marker_id)
        if found is None:
            raise MarkerError(f"unknown marker: {marker_id}")
        owner_kind, owner_id, marker = found
        updated = marker.with_metadata(color=color, note=note, event_type=event_type)
        if owner_kind is OwnerKind.CLIP:
            self._clips[owner_id].markers[marker_id] = updated
        else:
            self._timeline_markers[marker_id] = updated
        return updated

    def remove_marker(self, marker_id: str) -> Marker:
        found = self._find_marker(marker_id)
        if found is None:
            raise MarkerError(f"unknown marker: {marker_id}")
        owner_kind, owner_id, marker = found
        if owner_kind is OwnerKind.CLIP:
            del self._clips[owner_id].markers[marker_id]
        else:
            del self._timeline_markers[marker_id]
        return marker

    def marker_timeline_tick(self, marker_id: str) -> int:
        found = self._find_marker(marker_id)
        if found is None:
            raise MarkerError(f"unknown marker: {marker_id}")
        owner_kind, owner_id, marker = found
        if owner_kind is OwnerKind.TIMELINE:
            return marker.anchor_tick
        return self._clips[owner_id].marker_timeline_tick(marker)

    def marker_media_tick(self, marker_id: str) -> int | None:
        found = self._find_marker(marker_id)
        if found is None:
            raise MarkerError(f"unknown marker: {marker_id}")
        owner_kind, owner_id, marker = found
        if owner_kind is OwnerKind.TIMELINE:
            return None
        return self._clips[owner_id].marker_media_tick(marker)

    def marker_visible(self, marker_id: str) -> bool:
        found = self._find_marker(marker_id)
        if found is None:
            raise MarkerError(f"unknown marker: {marker_id}")
        owner_kind, owner_id, marker = found
        if owner_kind is OwnerKind.TIMELINE:
            return True
        return self._clips[owner_id].marker_visible(marker)

    def _occurrence(
        self, owner_kind: OwnerKind, owner_id: str, marker: Marker, clip: Clip | None = None
    ) -> MarkerOccurrence:
        if owner_kind is OwnerKind.TIMELINE:
            timeline_tick: int | None = marker.anchor_tick
            media_tick: int | None = None
            visible = True
        else:
            if clip is None:
                clip = self.clip(owner_id)
            timeline_tick = clip.marker_timeline_tick(marker)
            media_tick = clip.marker_media_tick(marker)
            visible = clip.marker_visible(marker)
        return MarkerOccurrence(
            id=marker.id,
            owner_kind=owner_kind,
            owner_id=owner_id,
            anchor=marker.anchor,
            anchor_tick=marker.anchor_tick,
            color=marker.color,
            note=marker.note,
            event_type=marker.event_type,
            timeline_tick=timeline_tick,
            media_tick=media_tick,
            visible=visible,
        )

    def occurrences(self, *, include_hidden: bool = True) -> tuple[MarkerOccurrence, ...]:
        resolved = [
            self._occurrence(OwnerKind.CLIP, clip.id, marker, clip)
            for clip in self._clips.values()
            for marker in clip.markers.values()
        ]
        resolved.extend(
            self._occurrence(OwnerKind.TIMELINE, "timeline", marker)
            for marker in self._timeline_markers.values()
        )
        if not include_hidden:
            resolved = [occurrence for occurrence in resolved if occurrence.visible]
        resolved.sort(
            key=lambda occurrence: (
                occurrence.timeline_tick if occurrence.timeline_tick is not None else 2**63,
                occurrence.id,
            )
        )
        return tuple(resolved)

    def markers_at_timeline_tick(
        self, timeline_tick: int, *, include_hidden: bool = False
    ) -> tuple[MarkerOccurrence, ...]:
        tick = _require_tick("timeline_tick", timeline_tick)
        return tuple(
            occurrence
            for occurrence in self.occurrences(include_hidden=include_hidden)
            if occurrence.timeline_tick == tick
        )
