"""Command-line interfaces for fast-start relocation and verification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from .errors import FragmentedMP4Error, MP4Error, VerificationError
from .relayout import faststart
from .verify import compare_sample_bytes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="faststart",
        description=(
            "Move a trailing non-fragmented MP4 moov box before mdat, repair "
            "stco/co64 offsets, and emit a JSON box movement table."
        ),
    )
    parser.add_argument("input", help="source MP4")
    parser.add_argument("output", help="destination MP4 (must differ from source)")
    parser.add_argument(
        "--move-table",
        metavar="PATH",
        help="also write the JSON movement table to PATH (use - for stdout)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="independently parse and compare every sample byte after relayout",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="do not print the movement table to stdout",
    )
    return parser


def faststart_main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = faststart(args.input, args.output)
        if args.verify:
            result.report["verification"] = compare_sample_bytes(
                args.input, args.output
            )
        rendered = result.to_json()
        if args.move_table:
            if args.move_table == "-":
                print(rendered)
            else:
                Path(args.move_table).write_text(rendered + "\n", encoding="utf-8")
        elif not args.quiet:
            print(rendered)
        if not args.quiet and args.move_table and args.move_table != "-":
            print(rendered)
        return 0
    except FragmentedMP4Error as exc:
        print(f"faststart: {exc}", file=sys.stderr)
        return 2
    except (MP4Error, OSError, VerificationError) as exc:
        print(f"faststart: {exc}", file=sys.stderr)
        return 2


def verify_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify-samples",
        description="Compare every MP4 sample byte using an independent table parser.",
    )
    parser.add_argument("original", help="original MP4")
    parser.add_argument("rebuilt", help="rewritten MP4")
    return parser


def verify_main(argv: Sequence[str] | None = None) -> int:
    args = verify_parser().parse_args(argv)
    try:
        report = compare_sample_bytes(args.original, args.rebuilt)
    except (MP4Error, OSError, VerificationError) as exc:
        print(f"verify-samples: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0
