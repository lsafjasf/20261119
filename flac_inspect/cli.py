"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys

from .parser import FlacFormatError, inspect_file
from .report import as_dict, render_text


def _healthy(result) -> bool:
    return (
        not result.errors
        and not result.invalid_frames
        and not result.recoveries
        and result.md5_matches is not False
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect FLAC metadata, frames, CRCs, and PCM MD5"
    )
    parser.add_argument("files", nargs="+", help="native FLAC file(s)")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    results = []
    fatal = False
    for filename in args.files:
        try:
            results.append(inspect_file(filename))
        except (OSError, FlacFormatError) as error:
            fatal = True
            print(f"{filename}: {error}", file=sys.stderr)

    if args.json:
        payload = [as_dict(result) for result in results]
        print(json.dumps(payload[0] if len(payload) == 1 else payload, indent=2))
    else:
        for index, result in enumerate(results):
            if index:
                print()
            print(render_text(result))

    if fatal:
        return 2
    return 0 if all(_healthy(result) for result in results) else 1
