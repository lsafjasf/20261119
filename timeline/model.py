"""Domain model for a media source, timeline clips and their internal markers.

Time is expressed in seconds (float) everywhere. A clip occupies a fixed
target (timeline) interval and points at a sliding source interval inside a
:class:`MediaSource`. Markers and keyframes are anchored to source time, so
slipping the source window remaps their displayed timeline time without
changing the clip duration.
"""

from __future__ import annotations

from dataclasses import dataclass, field

EPS = 1e-9


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def format_time(seconds: float) -> str:
    """Format seconds as ``HH:MM:SS.mmm`` for stable human-facing output."""
    total_ms = round(max(0.0, seconds) * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


@dataclass(frozen=True)
class MediaSource:
    """A piece of source media with a finite usable length."""

    name: str
    length: float

    def __post_init__(self) -> None:
        if self.length <= 0:
            raise ValueError("source length must be positive")


@dataclass(frozen=True)
class Marker:
    """A marker anchored at a source time, optionally carrying a label."""

    source_time: float
    label: str = ""

    def __post_init__(self) -> None:
        if self.source_time < -EPS:
            raise ValueError("marker source time cannot be negative")


@dataclass(frozen=True)
class Keyframe:
    """A keyframe anchored at a source time with an interpolatable value."""

    source_time: float
    value: float

    def __post_init__(self) -> None:
        if self.source_time < -EPS:
            raise ValueError("keyframe source time cannot be negative")


@dataclass
class Clip:
    """A clip with a fixed target interval and a sliding source window.

    ``target_start`` and ``duration`` never change during a slip; only
    ``source_in`` / ``source_out`` move together by the same delta.
    """

    clip_id: str
    source: MediaSource
    target_start: float
    source_in: float
    source_out: float
    markers: list[Marker] = field(default_factory=list)
    keyframes: list[Keyframe] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.duration <= 0:
            raise ValueError("clip duration must be positive")
        if self.target_start < -EPS:
            raise ValueError("target start cannot be negative")
        if self.source_in < -EPS or self.source_out < -EPS:
            raise ValueError("source endpoints cannot be negative")
        if self.source_out > self.source.length + EPS:
            raise ValueError("source out exceeds source length")
        if self.source_in + EPS >= self.source_out:
            raise ValueError("source in must precede source out")

    @property
    def duration(self) -> float:
        return self.source_out - self.source_in

    @property
    def target_end(self) -> float:
        return self.target_start + self.duration

    @property
    def head_room(self) -> float:
        """How far the source window can slip toward earlier media."""
        return self.source_in

    @property
    def tail_room(self) -> float:
        """How far the source window can slip toward later media."""
        return self.source.length - self.source_out

    def source_to_target(self, source_time: float) -> float:
        """Map a source time to the displayed timeline time."""
        return self.target_start + (source_time - self.source_in)

    def target_to_source(self, target_time: float) -> float:
        return self.source_in + (target_time - self.target_start)

    def visible_markers(self) -> list[tuple[Marker, float]]:
        """Return ``(marker, display_time)`` for markers inside the window."""
        return [
            (marker, self.source_to_target(marker.source_time))
            for marker in self.markers
            if self.source_in - EPS
            <= marker.source_time
            <= self.source_out + EPS
        ]

    def visible_keyframes(self) -> list[tuple[Keyframe, float]]:
        """Return ``(keyframe, display_time)`` for keyframes in the window."""
        return [
            (keyframe, self.source_to_target(keyframe.source_time))
            for keyframe in self.keyframes
            if self.source_in - EPS
            <= keyframe.source_time
            <= self.source_out + EPS
        ]

    def describe_intervals(self) -> dict[str, tuple[float, float]]:
        """Live source and target intervals, used by the view layer."""
        return {
            "source": (self.source_in, self.source_out),
            "target": (self.target_start, self.target_end),
        }


@dataclass
class Project:
    """A simple timeline holding clips and the undo history."""

    name: str
    clips: list[Clip] = field(default_factory=list)

    def get_clip(self, clip_id: str) -> Clip:
        for clip in self.clips:
            if clip.clip_id == clip_id:
                return clip
        raise KeyError(f"unknown clip: {clip_id}")

    def clip_view(self, clip_id: str) -> dict[str, object]:
        """Snapshot of everything displayed for a clip after window edits."""
        clip = self.get_clip(clip_id)
        return {
            "clip_id": clip.clip_id,
            "duration": clip.duration,
            "source_interval": (clip.source_in, clip.source_out),
            "target_interval": (clip.target_start, clip.target_end),
            "markers": [
                {
                    "label": marker.label,
                    "source_time": marker.source_time,
                    "display_time": display_time,
                }
                for marker, display_time in clip.visible_markers()
            ],
            "keyframes": [
                {
                    "value": keyframe.value,
                    "source_time": keyframe.source_time,
                    "display_time": display_time,
                }
                for keyframe, display_time in clip.visible_keyframes()
            ],
        }

