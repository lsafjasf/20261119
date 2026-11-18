"""Command-line entry points."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import StretchConfig, stretch_with_info
from .report import build_suite
from .wavio import read_wav, write_wav


def _speeds(values: list[float]) -> tuple[float, ...]:
    return tuple(float(value) for value in values)


def _cmd_demo(args: argparse.Namespace) -> int:
    report = build_suite(
        args.output,
        sample_rate=args.sample_rate,
        speeds=_speeds(args.speeds),
    )
    print(json.dumps(report["realtime"], indent=2, ensure_ascii=False))
    print(f"report: {Path(args.output).resolve() / 'index.html'}")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    audio, sample_rate = read_wav(args.input)
    config = StretchConfig(sample_rate=sample_rate, frame_size=args.frame_size)
    destination = Path(args.output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    for speed in _speeds(args.speeds):
        result = stretch_with_info(audio, speed, config)
        tag = f"{speed:g}".replace(".", "p")
        suffix = Path(args.input).stem
        path = destination / f"{suffix}_{tag}x.wav"
        write_wav(path, result.audio, sample_rate)
        print(
            json.dumps(
                {
                    "file": str(path),
                    "speed": speed,
                    "output_samples": int(result.audio.size),
                    "no_ola_holes": result.info.no_ola_holes,
                },
                ensure_ascii=False,
            )
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="varispeed",
        description="Pitch-preserving 0.5x-2.0x time stretching",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser(
        "demo", help="render tests, plots, audio, and the HTML report"
    )
    demo.add_argument("--output", default="artifacts")
    demo.add_argument("--sample-rate", type=int, default=44_100)
    demo.add_argument(
        "--speeds", nargs="+", type=float, default=[0.5, 1.0, 2.0]
    )
    demo.set_defaults(func=_cmd_demo)

    render = subparsers.add_parser("render", help="render a WAV at given speeds")
    render.add_argument("input")
    render.add_argument("--output-dir", default="artifacts/audio")
    render.add_argument(
        "--speeds", nargs="+", type=float, default=[0.5, 1.0, 2.0]
    )
    render.add_argument("--frame-size", type=int, default=2_048)
    render.set_defaults(func=_cmd_render)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
