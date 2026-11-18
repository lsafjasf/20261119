"""Slip trim: slide the source window without changing clip duration.

Dragging the in point moves the out point by the same delta. Requests that
would move the window past the available media are blocked at the boundary
(clamped, never overshooting). Markers and keyframes stay anchored to source
time and therefore remap to new display times automatically.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import EPS, Clip, clamp


@dataclass(frozen=True)
class SlipResult:
    """Outcome of a slip request, including any clamping that occurred."""

    requested_delta: float
    applied_delta: float
    blocked: bool
    source_in_before: float
    source_out_before: float
    source_in_after: float
    source_out_after: float


def clamp_slip_delta(clip: Clip, delta: float) -> tuple[float, bool]:
    """Clamp a slip delta to the media available before and after the window.

    Returns ``(applied_delta, blocked)`` where ``blocked`` indicates the
    request reached (and was stopped at) the head or tail of the source.
    """
    bounded = clamp(delta, -clip.head_room, clip.tail_room)
    blocked = abs(bounded - delta) > EPS
    return bounded, blocked


class SlipTrimCommand:
    """A single slip interaction, undoable and redoable as one unit."""

    def __init__(self, clip: Clip, requested_delta: float) -> None:
        self._clip = clip
        self._result = self._preview(clip, requested_delta)
        self._snapshot: tuple[float, float] | None = None

    @classmethod
    def drag_in_point(cls, clip: Clip, requested_in: float) -> "SlipTrimCommand":
        """Build a command from an absolute in-point drag position.

        The out point follows by the same delta; positions beyond the
        available media are clamped before the command is created.
        """
        return cls(clip, requested_in - clip.source_in)

    @staticmethod
    def _preview(clip: Clip, requested_delta: float) -> SlipResult:
        applied, blocked = clamp_slip_delta(clip, requested_delta)
        return SlipResult(
            requested_delta=requested_delta,
            applied_delta=applied,
            blocked=blocked,
            source_in_before=clip.source_in,
            source_out_before=clip.source_out,
            source_in_after=clip.source_in + applied,
            source_out_after=clip.source_out + applied,
        )

    @property
    def result(self) -> SlipResult:
        return self._result

    def do(self) -> SlipResult:
        if self._snapshot is None:
            self._snapshot = (self._clip.source_in, self._clip.source_out)
        self._clip.source_in = self._result.source_in_after
        self._clip.source_out = self._result.source_out_after
        return self._result

    redo = do

    def undo(self) -> None:
        if self._snapshot is None:
            raise RuntimeError("cannot undo a command that was never executed")
        old_in, old_out = self._snapshot
        self._clip.source_in = old_in
        self._clip.source_out = old_out
        self._assert_mapping_integrity()

    def _assert_mapping_integrity(self) -> None:
        # Markers/keyframes never move in source time; their display mapping
        # must always stay consistent with the restored endpoints.
        clip = self._clip
        for marker, display_time in clip.visible_markers():
            expected = clip.source_to_target(marker.source_time)
            if abs(display_time - expected) > EPS:
                raise AssertionError("marker mapping inconsistent after undo")
        for keyframe, display_time in clip.visible_keyframes():
            expected = clip.source_to_target(keyframe.source_time)
            if abs(display_time - expected) > EPS:
                raise AssertionError("keyframe mapping inconsistent after undo")


class History:
    """Undo/redo stack for slip (and other endpoint-preserving) commands."""

    def __init__(self) -> None:
        self._undo_stack: list[SlipTrimCommand] = []
        self._redo_stack: list[SlipTrimCommand] = []

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def execute(self, command: SlipTrimCommand) -> SlipResult:
        result = command.do()
        self._undo_stack.append(command)
        self._redo_stack.clear()
        return result

    def undo(self) -> None:
        if not self._undo_stack:
            raise RuntimeError("nothing to undo")
        command = self._undo_stack.pop()
        command.undo()
        self._redo_stack.append(command)

    def redo(self) -> None:
        if not self._redo_stack:
            raise RuntimeError("nothing to redo")
        command = self._redo_stack.pop()
        command.redo()
        self._undo_stack.append(command)
