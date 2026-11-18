"""Serialization of tracking trajectories, visibility and metrics."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Sequence

from .evaluation import evaluate_all
from .scenario import FrameTruth
from .tracker import TrackerResult, TrackFrame


def _record_payload(record: TrackFrame) -> dict:
    return {
        "frame": record.frame,
        "track_id": record.track_id,
        "decoded_id": record.decoded_id,
        "last_decoded_id": record.last_decoded_id,
        "decode_confidence": record.decode_confidence,
        "last_decoded_frame": record.last_decoded_frame,
        "visibility": record.visibility,
        "predicted": record.predicted,
        "corners": [
            [round(point.x, 4), round(point.y, 4)]
            for point in record.corners.corners
        ],
    }


def write_outputs(
    result: TrackerResult,
    truth: Sequence[FrameTruth],
    output_directory: str | Path,
) -> dict:
    root = Path(output_directory)
    root.mkdir(parents=True, exist_ok=True)
    grouped = result.by_track()
    tracks = []
    for track_id, records in sorted(grouped.items()):
        tracks.append(
            {
                "track_id": track_id,
                "last_decoded_id": records[-1].last_decoded_id,
                "first_frame": records[0].frame,
                "last_frame": records[-1].frame,
                "frames": [_record_payload(record) for record in records],
            }
        )
    (root / "tracks.json").write_text(
        json.dumps(
            {
                "tracks": tracks,
                "records": [_record_payload(record) for record in result.records],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    with (root / "trajectory.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "frame",
                "track_id",
                "last_decoded_id",
                "decoded_id",
                "decode_confidence",
                "visibility",
                "predicted",
                "x0",
                "y0",
                "x1",
                "y1",
                "x2",
                "y2",
                "x3",
                "y3",
            ]
        )
        for record in result.records:
            coordinates = [
                value
                for point in record.corners.corners
                for value in (round(point.x, 3), round(point.y, 3))
            ]
            writer.writerow(
                [
                    record.frame,
                    record.track_id,
                    record.last_decoded_id,
                    "" if record.decoded_id is None else record.decoded_id,
                    (
                        ""
                        if record.decode_confidence is None
                        else round(record.decode_confidence, 6)
                    ),
                    record.visibility,
                    int(record.predicted),
                    *coordinates,
                ]
            )

    metrics = evaluate_all(result, truth)
    with (root / "visibility.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "frame",
                "marker_id",
                "ground_truth_visibility",
                "visible_fraction",
                "track_visibility",
                "track_id",
                "predicted",
                "decoded_id",
                "decode_confidence",
            ]
        )
        for frame in metrics["per_frame_visibility"]:
            for marker in frame["markers"]:
                writer.writerow(
                    [
                        frame["frame"],
                        marker["marker_id"],
                        marker["ground_truth_visibility"],
                        round(marker["visible_fraction"], 6),
                        marker["track_visibility"],
                        "" if marker["track_id"] is None else marker["track_id"],
                        int(marker["predicted"]),
                        "" if marker["decoded_id"] is None else marker["decoded_id"],
                        (
                            ""
                            if marker["decode_confidence"] is None
                            else round(marker["decode_confidence"], 6)
                        ),
                    ]
                )
    (root / "summary.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metrics
