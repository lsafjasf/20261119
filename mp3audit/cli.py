"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys

from .parser import audit_file
from .report import render_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mp3audit",
        description=(
            "Validate MPEG audio frame chains, parse Layer III side information, "
            "and report bitrate, bit-reservoir, drift, and prefill damage."
        ),
    )
    parser.add_argument("file", help="MP3/MPEG audio file to inspect")
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the complete machine-readable report",
    )
    parser.add_argument(
        "--frames",
        action="store_true",
        help="include every frame in the text report",
    )
    parser.add_argument(
        "--fail-on-damage",
        action="store_true",
        help="exit with status 1 when a pre-decode defect is reported",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = audit_file(args.file)
    except OSError as exc:
        print(f"mp3audit: {exc}", file=sys.stderr)
        return 2

    if args.json:
        json.dump(result.to_dict(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(render_text(result, show_frames=args.frames))

    has_damage = bool(result.damaged_frames) or any(
        diagnostic.severity == "error" for diagnostic in result.diagnostics
    )
    return 1 if args.fail_on_damage and has_damage else 0
