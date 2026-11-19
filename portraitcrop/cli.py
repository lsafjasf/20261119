"""Command-line interface for sample generation, solving, and diagnosis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .diagnostics import diagnose, summarize_conflicts
from .io import load_annotations, load_pins, write_conflicts, write_json
from .media import find_ffmpeg, find_ffprobe, probe_video
from .models import make_crop_spec
from .pipeline import run_pipeline
from .report import write_markdown
from .samples import generate_scenario, get_scenario
from .solver import SolverConfig, solve_path


def _add_crop_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--aspect", default="9:16", help="target aspect (default 9:16)")
    parser.add_argument("--crop-width", type=int, help="explicit even crop width")
    parser.add_argument("--crop-height", type=int, help="explicit even crop height")
    parser.add_argument("--crop-y", type=int, help="crop top coordinate")


def _add_solver_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-speed", type=float, default=420.0, help="px/s")
    parser.add_argument("--max-accel", type=float, default=1400.0, help="px/s^2")
    parser.add_argument("--grid-step", type=int, default=1, help="pixel search step")
    parser.add_argument("--coverage-weight", type=float, default=20.0)
    parser.add_argument("--centering-weight", type=float, default=3.0)
    parser.add_argument("--velocity-weight", type=float, default=0.35)
    parser.add_argument("--acceleration-weight", type=float, default=4.0)
    parser.add_argument("--pin-weight", type=float, default=50.0)
    parser.add_argument("--min-confidence", type=float, default=0.0)


def _solver_config(args: argparse.Namespace) -> SolverConfig:
    return SolverConfig(
        max_speed_px_s=args.max_speed,
        max_accel_px_s2=args.max_accel,
        grid_step=args.grid_step,
        coverage_weight=args.coverage_weight,
        centering_weight=args.centering_weight,
        velocity_weight=args.velocity_weight,
        acceleration_weight=args.acceleration_weight,
        pin_weight=args.pin_weight,
        min_confidence=args.min_confidence,
    )


def _load_with_probe(
    rois_path: str | Path,
    video_path: str | Path | None,
    ffprobe: str | None,
) -> tuple[Any, dict[str, Any] | None]:
    metadata = None
    if video_path and ffprobe:
        metadata = probe_video(video_path, ffprobe)
    annotations = load_annotations(
        rois_path,
        width=metadata["width"] if metadata else None,
        height=metadata["height"] if metadata else None,
        fps=metadata["fps"] if metadata else None,
    )
    return annotations, metadata


def _print_summary(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def command_generate(args: argparse.Namespace) -> int:
    ffmpeg = find_ffmpeg(args.ffmpeg)
    scenario = get_scenario(
        args.scenario, width=args.width, height=args.height, fps=args.fps
    )
    result = generate_scenario(
        scenario, args.out, ffmpeg, crf=args.crf
    )
    _print_summary(result)
    return 0


def command_solve(args: argparse.Namespace) -> int:
    ffmpeg = None
    if not args.no_video:
        ffmpeg = find_ffmpeg(args.ffmpeg)
    ffprobe = find_ffprobe(args.ffprobe, ffmpeg)
    annotations, metadata = _load_with_probe(args.rois, args.video, ffprobe)
    crop = make_crop_spec(
        annotations.width,
        annotations.height,
        aspect=args.aspect,
        crop_width=args.crop_width,
        crop_height=args.crop_height,
        y=args.crop_y,
    )
    config = _solver_config(args)
    pins = load_pins(args.pins, annotations.fps)
    summary = run_pipeline(
        args.video,
        annotations,
        crop,
        config,
        pins,
        args.out,
        title=f"Portrait crop: {Path(args.video).name}",
        ffmpeg=ffmpeg,
        export_video=not args.no_video,
        keep_audio=not args.no_audio,
        crf=args.crf,
    )
    if metadata:
        summary["video_metadata"] = metadata
    _print_summary(summary)
    return 0 if summary["status"] == "ok" else 1


def command_diagnose(args: argparse.Namespace) -> int:
    annotations = load_annotations(args.rois)
    crop = make_crop_spec(
        annotations.width,
        annotations.height,
        aspect=args.aspect,
        crop_width=args.crop_width,
        crop_height=args.crop_height,
        y=args.crop_y,
    )
    config = _solver_config(args)
    pins = load_pins(args.pins, annotations.fps)
    result = solve_path(annotations, crop, config, pins)
    conflicts = diagnose(
        annotations, crop, result, min_confidence=config.min_confidence
    )
    summary = summarize_conflicts(conflicts)
    write_conflicts(
        args.out,
        conflicts,
        crop_width=crop.width,
        crop_height=crop.height,
        summary=summary,
    )
    _print_summary(
        {
            "status": result.status,
            "crop": {"width": crop.width, "height": crop.height},
            "conflict_count": len(conflicts),
            "conflict_groups": summary,
            "output": str(Path(args.out)),
        }
    )
    return 0


def command_demo(args: argparse.Namespace) -> int:
    ffmpeg = find_ffmpeg(args.ffmpeg)
    ffprobe = find_ffprobe(args.ffprobe, ffmpeg)
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    all_runs: dict[str, Any] = {}
    for scenario_name in ("dual_crossing", "side_entry"):
        scenario = get_scenario(
            scenario_name,
            width=args.width,
            height=args.height,
            fps=args.fps,
        )
        sample_dir = root / scenario_name
        print(f"[demo] generating {scenario_name}...", file=sys.stderr)
        generated = generate_scenario(
            scenario, sample_dir, ffmpeg, crf=args.crf
        )
        annotations = load_annotations(
            generated["annotation"],
            width=scenario.width,
            height=scenario.height,
            fps=scenario.fps,
        )
        crop = make_crop_spec(scenario.width, scenario.height, aspect="9:16")
        config = SolverConfig(
            max_speed_px_s=scenario.max_speed_px_s,
            max_accel_px_s2=scenario.max_accel_px_s2,
        )
        pins = load_pins(generated["pins"], annotations.fps)
        print(f"[demo] solving and exporting {scenario_name}...", file=sys.stderr)
        run = run_pipeline(
            generated["source_video"],
            annotations,
            crop,
            config,
            pins,
            sample_dir,
            title=scenario.title,
            ffmpeg=ffmpeg,
            export_video=True,
            keep_audio=False,
            crf=args.crf,
        )
        if ffprobe:
            portrait = run.get("export")
            if portrait and Path(portrait["path"]).exists():
                run["export"]["probe"] = probe_video(portrait["path"], ffprobe)
        all_runs[scenario_name] = run
    write_json(root / "demo_summary.json", {"scenarios": all_runs})
    index_lines = [
        "# PortraitCrop demo artifacts",
        "",
        "| 场景 | 状态 | 平均覆盖率 | 最低覆盖率 | 抖动 RMS | 越界帧 | 冲突记录 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for name, run in all_runs.items():
        coverage = run["metrics"].get("coverage", {})
        motion = run["metrics"].get("motion", {})
        constraints = run["metrics"].get("constraints", {})
        index_lines.append(
            f"| [`{name}`]({name}/report.md) | {run['status']} | "
            f"{100.0 * coverage.get('mean_weighted', 0.0):.2f}% | "
            f"{100.0 * coverage.get('min_weighted', 0.0):.2f}% | "
            f"{motion.get('jitter_rms_px_s2', 0.0):.2f} px/s² | "
            f"{constraints.get('out_of_bounds_frames', 0)} | "
            f"{run['conflict_count']} |"
        )
    index_lines.extend(
        [
            "",
            "每个场景目录包含 `source.mp4`、`rois.json`、`pins.json`、"
            "`portrait.mp4`、`windows.csv/json`、`metrics.json`、"
            "`conflicts.json` 和 `report.md`。",
            "",
        ]
    )
    write_markdown(root / "README.md", "\n".join(index_lines))
    overall_status = (
        "ok"
        if all(run["status"] == "ok" for run in all_runs.values())
        else "partial"
    )
    _print_summary(
        {
            "status": overall_status,
            "output_dir": str(root),
            "index": str(root / "README.md"),
            "scenarios": {
                name: {
                    "status": run["status"],
                    "coverage": run["metrics"].get("coverage", {}).get(
                        "mean_weighted"
                    ),
                    "jitter_rms_px_s2": run["metrics"]
                    .get("motion", {})
                    .get("jitter_rms_px_s2"),
                    "out_of_bounds_frames": run["metrics"]
                    .get("constraints", {})
                    .get("out_of_bounds_frames"),
                    "conflict_count": run["conflict_count"],
                }
                for name, run in all_runs.items()
            },
        }
    )
    return 0 if overall_status == "ok" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="portraitcrop",
        description="Content-aware portrait crop path solver",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="generate a sample video")
    generate.add_argument(
        "--scenario",
        required=True,
        choices=("dual_crossing", "side_entry"),
    )
    generate.add_argument("--out", required=True)
    generate.add_argument("--ffmpeg")
    generate.add_argument("--width", type=int, default=960)
    generate.add_argument("--height", type=int, default=540)
    generate.add_argument("--fps", type=int, default=30)
    generate.add_argument("--crf", type=int, default=23)
    generate.set_defaults(handler=command_generate)

    solve = subparsers.add_parser("solve", help="solve and export one video")
    solve.add_argument("--video", required=True)
    solve.add_argument("--rois", required=True)
    solve.add_argument("--pins")
    solve.add_argument("--out", required=True)
    solve.add_argument("--ffmpeg")
    solve.add_argument("--ffprobe")
    solve.add_argument("--no-video", action="store_true")
    solve.add_argument("--no-audio", action="store_true")
    solve.add_argument("--crf", type=int, default=20)
    _add_crop_arguments(solve)
    _add_solver_arguments(solve)
    solve.set_defaults(handler=command_solve)

    diagnose_parser = subparsers.add_parser(
        "diagnose", help="report incompatible interest regions"
    )
    diagnose_parser.add_argument("--rois", required=True)
    diagnose_parser.add_argument("--pins")
    diagnose_parser.add_argument("--out", required=True)
    _add_crop_arguments(diagnose_parser)
    _add_solver_arguments(diagnose_parser)
    diagnose_parser.set_defaults(handler=command_diagnose)

    demo = subparsers.add_parser(
        "demo", help="generate and process both sample scenarios"
    )
    demo.add_argument("--out", default="artifacts/demo")
    demo.add_argument("--ffmpeg")
    demo.add_argument("--ffprobe")
    demo.add_argument("--width", type=int, default=960)
    demo.add_argument("--height", type=int, default=540)
    demo.add_argument("--fps", type=int, default=30)
    demo.add_argument("--crf", type=int, default=23)
    demo.set_defaults(handler=command_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (ValueError, RuntimeError, OSError) as error:
        print(f"portraitcrop: {error}", file=sys.stderr)
        return 2
