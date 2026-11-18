"""Ground-truth evaluation for detection corners and crossing identities."""

from __future__ import annotations

import math
from typing import Sequence

from .scenario import FrameTruth
from .tracker import FrameObservation, TrackerResult, TrackFrame


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def error_stats(errors: Sequence[float]) -> dict[str, float | int]:
    if not errors:
        return {
            "count": 0,
            "mean_px": 0.0,
            "median_px": 0.0,
            "p95_px": 0.0,
            "max_px": 0.0,
            "within_2px": 0.0,
            "within_4px": 0.0,
        }
    return {
        "count": len(errors),
        "mean_px": sum(errors) / len(errors),
        "median_px": _percentile(errors, 0.50),
        "p95_px": _percentile(errors, 0.95),
        "max_px": max(errors),
        "within_2px": sum(value <= 2.0 for value in errors) / len(errors),
        "within_4px": sum(value <= 4.0 for value in errors) / len(errors),
    }


def _mean_corner_error(left, right) -> float:
    return sum(
        a.distance_to(b) for a, b in zip(left.corners, right.corners)
    ) / 4.0


def evaluate_detections(
    observations: Sequence[FrameObservation], truth: Sequence[FrameTruth]
) -> dict:
    truth_lookup = {
        (frame.frame, marker.marker_id): marker
        for frame in truth
        for marker in frame.markers
    }
    errors: list[float] = []
    confidence: list[float] = []
    detected_visible = 0
    visible_total = 0
    false_positives = 0
    decodes_on_occluded = 0
    for frame in truth:
        for marker in frame.markers:
            if marker.visible_fraction >= 0.75:
                visible_total += 1
                if any(
                    item.frame == frame.frame and item.marker_id == marker.marker_id
                    for item in observations
                ):
                    detected_visible += 1
    for observation in observations:
        confidence.append(observation.confidence)
        matched = truth_lookup.get((observation.frame, observation.marker_id))
        if matched is None:
            false_positives += 1
            continue
        errors.append(_mean_corner_error(observation.corners, matched.corners))
        if matched.visible_fraction < 0.15:
            decodes_on_occluded += 1
    stats = error_stats(errors)
    stats["false_positives"] = false_positives
    stats["decodes_on_occluded_gt"] = decodes_on_occluded
    stats["visible_marker_frames"] = visible_total
    stats["detected_visible_marker_frames"] = detected_visible
    stats["visible_recall"] = (
        detected_visible / visible_total if visible_total else 1.0
    )
    stats["mean_confidence"] = (
        sum(confidence) / len(confidence) if confidence else 0.0
    )
    return stats


def crossing_window(truth: Sequence[FrameTruth]) -> list[int]:
    if not truth or len(truth[0].markers) < 2:
        return []
    distances: dict[int, float] = {}
    sizes: list[float] = []
    for frame in truth:
        first, second = frame.markers[0], frame.markers[1]
        distances[frame.frame] = first.corners.center().distance_to(
            second.corners.center()
        )
        sizes.append(first.corners.mean_side())
        sizes.append(second.corners.mean_side())
    threshold = 1.45 * (sum(sizes) / len(sizes))
    window = [frame for frame, distance in distances.items() if distance <= threshold]
    return window


def _track_id_switches(records: Sequence[TrackFrame]) -> int:
    previous: int | None = None
    switches = 0
    for record in sorted(records, key=lambda item: item.frame):
        if record.decoded_id is None:
            continue
        if previous is not None and record.decoded_id != previous:
            switches += 1
        previous = record.decoded_id
    return switches


def evaluate_tracking(
    result: TrackerResult, truth: Sequence[FrameTruth]
) -> dict:
    truth_lookup = {
        (frame.frame, marker.marker_id): marker
        for frame in truth
        for marker in frame.markers
    }
    grouped = result.by_track()
    track_errors: list[float] = []
    visible_errors: list[float] = []
    decoded_on_occluded = 0
    occluded_frames = 0
    predicted_occluded_frames = 0
    lost_occluded_frames = 0
    id_switches = 0
    identity_pairs: dict[int, list[int]] = {}
    for track_id, records in grouped.items():
        id_switches += _track_id_switches(records)
        decoded = [record.decoded_id for record in records if record.decoded_id is not None]
        identity_pairs[track_id] = decoded
        for record in records:
            marker = truth_lookup.get((record.frame, record.last_decoded_id))
            if marker is None:
                continue
            error = _mean_corner_error(record.corners, marker.corners)
            track_errors.append(error)
            if record.visibility == "visible":
                visible_errors.append(error)
            if record.decoded_id is not None and marker.visible_fraction < 0.15:
                decoded_on_occluded += 1
            if marker.visible_fraction < 0.20:
                occluded_frames += 1
                if record.visibility == "occluded":
                    predicted_occluded_frames += 1
                elif record.visibility == "lost":
                    lost_occluded_frames += 1
    window = set(crossing_window(truth))
    window_records = [record for record in result.records if record.frame in window]
    window_switches = 0
    for record in window_records:
        marker = truth_lookup.get((record.frame, record.last_decoded_id))
        if marker is None:
            continue
        if record.decoded_id is not None and record.decoded_id != record.last_decoded_id:
            window_switches += 1
    window_track_ids: dict[int, set[int]] = {}
    for record in window_records:
        if record.decoded_id is not None:
            window_track_ids.setdefault(record.track_id, set()).add(record.decoded_id)
    swapped_in_window = any(
        len(identities) != 1 for identities in window_track_ids.values()
    )
    ids_expected = sorted({marker.marker_id for frame in truth for marker in frame.markers})
    ids_tracked = sorted(
        {
            record.last_decoded_id
            for record in result.records
            if record.visibility == "visible"
        }
    )
    fresh_sequences = {
        track_id: sorted(set(identifiers))
        for track_id, identifiers in identity_pairs.items()
    }
    checks = {
        "all_marker_ids_tracked": ids_tracked == ids_expected,
        "track_count_matches_markers": len(grouped) == len(ids_expected),
        "ids_stable": id_switches == 0,
        "crossing_ids_stable": window_switches == 0 and not swapped_in_window,
        "no_fresh_decode_while_occluded": decoded_on_occluded == 0,
        "short_occlusion_predicted": (
            occluded_frames == 0
            or predicted_occluded_frames >= occluded_frames - lost_occluded_frames
        ),
    }
    return {
        "track_count": len(grouped),
        "expected_marker_count": len(ids_expected),
        "tracked_marker_ids": ids_tracked,
        "fresh_id_sets": fresh_sequences,
        "id_switches": id_switches,
        "crossing_window_frames": sorted(window),
        "id_switches_in_crossing_window": window_switches,
        "swapped_in_crossing_window": swapped_in_window,
        "occluded_ground_truth_frames": occluded_frames,
        "predicted_occlusion_frames": predicted_occluded_frames,
        "lost_occlusion_frames": lost_occluded_frames,
        "fresh_decodes_while_occluded": decoded_on_occluded,
        "corner_error_px": error_stats(track_errors),
        "visible_corner_error_px": error_stats(visible_errors),
        "checks": checks,
    }


def per_frame_visibility(
    result: TrackerResult, truth: Sequence[FrameTruth]
) -> list[dict]:
    grouped = result.by_frame()
    output: list[dict] = []
    for frame in truth:
        frame_records = grouped.get(frame.frame, [])
        markers = []
        for marker in frame.markers:
            record = next(
                (
                    item
                    for item in frame_records
                    if item.last_decoded_id == marker.marker_id
                ),
                None,
            )
            markers.append(
                {
                    "marker_id": marker.marker_id,
                    "ground_truth_visibility": marker.visibility,
                    "visible_fraction": marker.visible_fraction,
                    "track_visibility": record.visibility if record else "untracked",
                    "track_id": record.track_id if record else None,
                    "predicted": record.predicted if record else False,
                    "decoded_id": record.decoded_id if record else None,
                    "decode_confidence": (
                        record.decode_confidence if record else None
                    ),
                }
            )
        output.append({"frame": frame.frame, "markers": markers})
    return output


def evaluate_all(
    result: TrackerResult, truth: Sequence[FrameTruth]
) -> dict:
    return {
        "detection": evaluate_detections(result.observations, truth),
        "tracking": evaluate_tracking(result, truth),
        "per_frame_visibility": per_frame_visibility(result, truth),
    }
