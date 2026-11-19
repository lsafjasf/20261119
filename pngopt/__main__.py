"""Command line interface for the PNG filter strategy optimizer."""

from __future__ import annotations

import argparse
import sys

from .optimizer import (
    STRATEGIES,
    beam_search,
    heuristic_greedy,
    lookahead_search,
    precompute_variants,
)
from .png import UnsupportedInterlaceError, build_png, parse_png
from .report import build_and_measure, format_report


def _load(path: str):
    with open(path, "rb") as handle:
        data = handle.read()
    return data, parse_png(data)


def cmd_optimize(args) -> int:
    try:
        original, parsed = _load(args.input)
    except UnsupportedInterlaceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    _, variants = precompute_variants(parsed)
    if args.strategy == "heuristic":
        result = heuristic_greedy(parsed, args.level, variants=variants)
    elif args.strategy == "lookahead":
        result = lookahead_search(parsed, args.level, args.window,
                                  variants=variants)
    else:
        result = beam_search(parsed, args.level, args.beam_width,
                             variants=variants)
    measurement = build_and_measure(parsed, result, original,
                                    repeats=args.repeats)
    if not measurement["verification"]["ok"]:
        print("error: round-trip verification failed; refusing to write",
              file=sys.stderr)
        return 3
    output = build_png(parsed, result.payload, idat_limit=args.idat_limit)
    with open(args.output, "wb") as handle:
        handle.write(output)
    print(format_report(args.input, original, parsed, [measurement]))
    print(f"wrote {args.output} ({len(output)} bytes)")
    return 0


def cmd_benchmark(args) -> int:
    from .benchmark import run_benchmark
    report = run_benchmark(
        out_dir=args.out_dir,
        size=args.size,
        level=args.level,
        window=args.window,
        beam_width=args.beam_width,
        repeats=args.repeats)
    print(report)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pngopt",
        description="Re-select PNG scanline filters without altering pixels "
                    "or ancillary chunks.")
    sub = parser.add_subparsers(dest="command", required=True)

    opt = sub.add_parser("optimize", help="optimize one PNG file")
    opt.add_argument("input")
    opt.add_argument("output")
    opt.add_argument("--strategy", choices=sorted(STRATEGIES),
                     default="beam")
    opt.add_argument("--level", type=int, default=9, choices=range(1, 10),
                     metavar="1-9")
    opt.add_argument("--window", type=int, default=4,
                     help="lookahead window rows (lookahead strategy)")
    opt.add_argument("--beam-width", type=int, default=8,
                     help="beam width (beam strategy)")
    opt.add_argument("--idat-limit", type=int, default=8192)
    opt.add_argument("--repeats", type=int, default=20,
                     help="decode timing repetitions")
    opt.set_defaults(func=cmd_optimize)

    bench = sub.add_parser("benchmark",
                           help="generate patterns, optimize and compare")
    bench.add_argument("--out-dir", default="bench_out")
    bench.add_argument("--size", type=int, default=256)
    bench.add_argument("--level", type=int, default=9, choices=range(1, 10),
                       metavar="1-9")
    bench.add_argument("--window", type=int, default=4)
    bench.add_argument("--beam-width", type=int, default=8)
    bench.add_argument("--repeats", type=int, default=20)
    bench.set_defaults(func=cmd_benchmark)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except UnsupportedInterlaceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
