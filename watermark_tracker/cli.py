"""Command line interface for the synthetic clip, tracker and evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .detector import detect_markers
from .evaluation import evaluate_all
from .report import write_outputs
from .scenario import (
    SyntheticClip,
    generate_crossing_clip,
    load_frame_sequence,
    load_ground_truth,
)
from .tracker import FrameObservation, Tracker


def _run_dataset(dataset: Path, output: Path, max_prediction_frames: int) -> dict:
    truth = load_ground_truth(dataset / "ground_truth.json")
    tracker = Tracker(max_prediction_frames=max_prediction_frames)
    frame_count = 0
    for frame, image in enumerate(load_frame_sequence(dataset)):
        detections = detect_markers(image)
        observations = [
            FrameObservation(frame, item.marker_id, item.corners, item.confidence)
            for item in detections
        ]
        tracker.process_frame(frame, observations)
        frame_count += 1
    if frame_count != len(truth):
        raise ValueError(
            f"dataset has {frame_count} frames but {len(truth)} truth entries"
        )
    result = tracker.result()
    metrics = write_outputs(result, truth, output)
    return metrics


def _print_summary(metrics: dict) -> None:
    detection = metrics["detection"]
    tracking = metrics["tracking"]
    print(
        "detection: "
        f"{detection['count']} observations, "
        f"mean corner error {detection['mean_px']:.3f} px, "
        f"recall {detection['visible_recall']:.3f}"
    )
    print(
        "tracking: "
        f"{tracking['track_count']} tracks, "
        f"id switches {tracking['id_switches']}, "
        f"occlusion predictions {tracking['predicted_occlusion_frames']}/"
        f"{tracking['occluded_ground_truth_frames']}, "
        f"fresh occluded decodes {tracking['fresh_decodes_while_occluded']}"
    )
    print("checks:", json.dumps(tracking["checks"], sort_keys=True))


def _generate(args: argparse.Namespace) -> SyntheticClip:
    clip = generate_crossing_clip(
        frame_count=args.frames,
        width=args.width,
        height=args.height,
        seed=args.seed,
    )
    clip.save(args.output)
    clip.write_avi(args.output / "clip.avi")
    print(f"wrote {len(clip.images)} frames and ground truth to {args.output}")
    return clip


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="watermark-tracker",
        description="Error-corrected square marker tracking and evaluation",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="generate the crossing clip")
    generate.add_argument("output", type=Path)
    generate.add_argument("--frames", type=int, default=72)
    generate.add_argument("--width", type=int, default=360)
    generate.add_argument("--height", type=int, default=240)
    generate.add_argument("--seed", type=int, default=20261119)

    run = subparsers.add_parser("run", help="track an existing PGM frame dataset")
    run.add_argument("dataset", type=Path)
    run.add_argument("output", type=Path)
    run.add_argument("--max-prediction-frames", type=int, default=5)

    demo = subparsers.add_parser("demo", help="generate and evaluate in one command")
    demo.add_argument("output", type=Path)
    demo.add_argument("--frames", type=int, default=72)
    demo.add_argument("--seed", type=int, default=20261119)
    demo.add_argument("--max-prediction-frames", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "generate":
        _generate(args)
        return 0
    if args.command == "run":
        metrics = _run_dataset(args.dataset, args.output, args.max_prediction_frames)
        _print_summary(metrics)
        return 0
    root = args.output
    clip = generate_crossing_clip(
        frame_count=args.frames, seed=args.seed
    )
    dataset = root / "clip"
    clip.save(dataset)
    clip.write_avi(dataset / "clip.avi")
    metrics = _run_dataset(dataset, root / "results", args.max_prediction_frames)
    _print_summary(metrics)
    print(f"wrote clip to {dataset} and results to {root / 'results'}")
    return 0
