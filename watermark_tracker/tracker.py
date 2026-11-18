"""Multi-marker tracking with explicit short-occlusion predictions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .geometry import Point, Quad

INFINITE_COST = 1e12


@dataclass(frozen=True)
class FrameObservation:
    frame: int
    marker_id: int
    corners: Quad
    confidence: float


@dataclass(frozen=True)
class TrackFrame:
    frame: int
    track_id: int
    decoded_id: int | None
    last_decoded_id: int
    decode_confidence: float | None
    last_decoded_frame: int
    visibility: str
    corners: Quad
    predicted: bool


@dataclass
class _Track:
    track_id: int
    marker_id: int
    observed_corners: Quad
    observed_frame: int
    confidence: float
    last_decoded_frame: int
    velocity: tuple[Point, Point, Point, Point] = field(
        default_factory=lambda: (Point(0, 0),) * 4
    )
    missed: int = 0

    def predicted_corners(self, frame: int) -> Quad:
        gap = max(0, frame - self.observed_frame)
        corners = tuple(
            Point(point.x + velocity.x * gap, point.y + velocity.y * gap)
            for point, velocity in zip(self.observed_corners.corners, self.velocity)
        )
        return Quad(*corners)


def _distance(left: Quad, right: Quad) -> float:
    return sum(
        a.distance_to(b) for a, b in zip(left.corners, right.corners)
    ) / 4.0


def _hungarian(cost: Sequence[Sequence[float]]) -> list[tuple[int, int]]:
    """Minimum-cost assignment for a rectangular dense matrix."""
    if not cost or not cost[0]:
        return []
    row_count = len(cost)
    column_count = len(cost[0])
    transpose = row_count > column_count
    matrix = (
        [[cost[row][column] for row in range(row_count)] for column in range(column_count)]
        if transpose
        else [list(row) for row in cost]
    )
    n = len(matrix)
    m = len(matrix[0])
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for row in range(1, n + 1):
        p[0] = row
        column0 = 0
        minimum = [float("inf")] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[column0] = True
            row0 = p[column0]
            delta = float("inf")
            column1 = 0
            for column in range(1, m + 1):
                if used[column]:
                    continue
                current = matrix[row0 - 1][column - 1] - u[row0] - v[column]
                if current < minimum[column]:
                    minimum[column] = current
                    way[column] = column0
                if minimum[column] < delta:
                    delta = minimum[column]
                    column1 = column
            for column in range(m + 1):
                if used[column]:
                    u[p[column]] += delta
                    v[column] -= delta
                else:
                    minimum[column] -= delta
            column0 = column1
            if p[column0] == 0:
                break
        while True:
            column1 = way[column0]
            p[column0] = p[column1]
            column0 = column1
            if column0 == 0:
                break
    assignment: list[tuple[int, int]] = []
    for column in range(1, m + 1):
        if p[column] == 0:
            continue
        row = p[column] - 1
        col = column - 1
        assignment.append((col, row) if transpose else (row, col))
    return assignment


class Tracker:
    def __init__(self, max_prediction_frames: int = 5, gate_ratio: float = 1.0):
        self.max_prediction_frames = max_prediction_frames
        self.gate_ratio = gate_ratio
        self._tracks: list[_Track] = []
        self._next_track_id = 1
        self._records: list[TrackFrame] = []
        self._observations: list[FrameObservation] = []

    @property
    def tracks(self) -> list[_Track]:
        return list(self._tracks)

    def process_frame(
        self, frame: int, observations: Sequence[FrameObservation]
    ) -> list[TrackFrame]:
        tracks = list(self._tracks)
        observations = [
            observation
            for observation in observations
            if observation.frame == frame
        ]
        self._observations.extend(observations)
        predictions = [track.predicted_corners(frame) for track in tracks]
        cost: list[list[float]] = []
        for track, prediction in zip(tracks, predictions):
            row = []
            for observation in observations:
                if observation.marker_id != track.marker_id:
                    row.append(INFINITE_COST)
                    continue
                size = max(prediction.mean_side(), observation.corners.mean_side())
                distance = _distance(prediction, observation.corners)
                allowed = size * (self.gate_ratio + 0.35 * track.missed)
                if distance > allowed:
                    row.append(INFINITE_COST)
                else:
                    row.append(
                        distance / max(size, 1.0)
                        + 0.05 * (1.0 - observation.confidence)
                    )
            cost.append(row)
        assignments = _hungarian(cost) if cost and observations else []
        matched_tracks: set[int] = set()
        matched_observations: set[int] = set()
        frame_records: list[TrackFrame] = []
        for track_index, observation_index in assignments:
            if cost[track_index][observation_index] >= INFINITE_COST / 2:
                continue
            track = tracks[track_index]
            observation = observations[observation_index]
            gap = max(1, frame - track.observed_frame)
            measured_velocity = tuple(
                Point(
                    (new.x - old.x) / gap,
                    (new.y - old.y) / gap,
                )
                for old, new in zip(
                    track.observed_corners.corners, observation.corners.corners
                )
            )
            blend = 0.35 if track.observed_frame != frame and track.missed == 0 else 0.65
            track.velocity = tuple(
                Point(
                    old.x * (1.0 - blend) + new.x * blend,
                    old.y * (1.0 - blend) + new.y * blend,
                )
                for old, new in zip(track.velocity, measured_velocity)
            )
            track.observed_corners = observation.corners
            track.observed_frame = frame
            track.confidence = observation.confidence
            track.last_decoded_frame = frame
            track.missed = 0
            matched_tracks.add(track_index)
            matched_observations.add(observation_index)
            frame_records.append(
                TrackFrame(
                    frame=frame,
                    track_id=track.track_id,
                    decoded_id=observation.marker_id,
                    last_decoded_id=track.marker_id,
                    decode_confidence=observation.confidence,
                    last_decoded_frame=track.last_decoded_frame,
                    visibility="visible",
                    corners=observation.corners,
                    predicted=False,
                )
            )

        remaining_tracks: list[_Track] = []
        for index, track in enumerate(tracks):
            if index in matched_tracks:
                remaining_tracks.append(track)
                continue
            track.missed += 1
            if track.missed <= self.max_prediction_frames:
                remaining_tracks.append(track)
                frame_records.append(
                    TrackFrame(
                        frame=frame,
                        track_id=track.track_id,
                        decoded_id=None,
                        last_decoded_id=track.marker_id,
                        decode_confidence=None,
                        last_decoded_frame=track.last_decoded_frame,
                        visibility="occluded",
                        corners=track.predicted_corners(frame),
                        predicted=True,
                    )
                )
            else:
                frame_records.append(
                    TrackFrame(
                        frame=frame,
                        track_id=track.track_id,
                        decoded_id=None,
                        last_decoded_id=track.marker_id,
                        decode_confidence=None,
                        last_decoded_frame=track.last_decoded_frame,
                        visibility="lost",
                        corners=track.predicted_corners(frame),
                        predicted=True,
                    )
                )

        for index, observation in enumerate(observations):
            if index in matched_observations:
                continue
            track = _Track(
                track_id=self._next_track_id,
                marker_id=observation.marker_id,
                observed_corners=observation.corners,
                observed_frame=frame,
                confidence=observation.confidence,
                last_decoded_frame=frame,
            )
            self._next_track_id += 1
            remaining_tracks.append(track)
            frame_records.append(
                TrackFrame(
                    frame=frame,
                    track_id=track.track_id,
                    decoded_id=observation.marker_id,
                    last_decoded_id=track.marker_id,
                    decode_confidence=observation.confidence,
                    last_decoded_frame=frame,
                    visibility="visible",
                    corners=observation.corners,
                    predicted=False,
                )
            )
        self._tracks = remaining_tracks
        frame_records.sort(key=lambda item: item.track_id)
        self._records.extend(frame_records)
        return frame_records

    def result(self) -> "TrackerResult":
        return TrackerResult(list(self._records), list(self._observations))


@dataclass
class TrackerResult:
    records: list[TrackFrame]
    observations: list[FrameObservation]

    def by_track(self) -> dict[int, list[TrackFrame]]:
        grouped: dict[int, list[TrackFrame]] = {}
        for record in self.records:
            grouped.setdefault(record.track_id, []).append(record)
        return grouped

    def by_frame(self) -> dict[int, list[TrackFrame]]:
        grouped: dict[int, list[TrackFrame]] = {}
        for record in self.records:
            grouped.setdefault(record.frame, []).append(record)
        return grouped
