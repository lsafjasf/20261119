"""Command-line entry points for edit-list validation and demos."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .edit_list import dumps_edit_list, load_edit_list, read_edit_list, write_edit_list
from .model import MarkerError, Timeline
from .timecode import format_timecode, seconds_to_ticks


def _build_demo() -> Timeline:
    timeline = Timeline()
    timeline.add_clip(
        "camera-a.mov",
        timeline_start=seconds_to_ticks(10),
        source_in=seconds_to_ticks(100),
        source_out=seconds_to_ticks(110),
        clip_id="clip-a",
    )
    timeline.add_media_marker(
        "clip-a",
        seconds_to_ticks(102),
        marker_id="media-sync",
        color="#ff4d4f",
        note="Keep the source sync point",
        event_type="sync",
    )
    timeline.add_clip_anchor_marker(
        "clip-a",
        seconds_to_ticks(5),
        marker_id="clip-cue",
        color="#1677ff",
        note="Five seconds into the clip",
        event_type="cue",
    )
    timeline.add_timeline_marker(
        seconds_to_ticks(20),
        marker_id="global-chapter",
        color="#52c41a",
        note="Absolute timeline chapter",
        event_type="chapter",
    )
    return timeline


def _print_markers(timeline: Timeline) -> None:
    for occurrence in timeline.occurrences(include_hidden=True):
        media = (
            "-"
            if occurrence.media_tick is None
            else format_timecode(occurrence.media_tick)
        )
        timeline_tick = (
            "-"
            if occurrence.timeline_tick is None
            else format_timecode(occurrence.timeline_tick)
        )
        visibility = "visible" if occurrence.visible else "hidden"
        print(
            f"{occurrence.id}\t{occurrence.anchor.value}\t"
            f"{timeline_tick}\tmedia={media}\t{visibility}\t"
            f"{occurrence.event_type}\t{occurrence.color}\t{occurrence.note}"
        )


def _read_or_stdin(path: str) -> Timeline:
    if path == "-":
        return load_edit_list(sys.stdin.buffer.read())
    return read_edit_list(path)


def _write_or_stdout(timeline: Timeline, path: str | None) -> None:
    if path is None or path == "-":
        sys.stdout.write(dumps_edit_list(timeline))
    else:
        write_edit_list(timeline, Path(path))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="timeline-markers",
        description="Validate and round-trip timeline marker edit lists.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("demo", help="emit a marker demo edit list")
    demo.add_argument("-o", "--output", help="output JSON path (default: stdout)")

    show = subparsers.add_parser("show", help="list markers from an edit list")
    show.add_argument("input", help="input JSON path, or - for stdin")

    roundtrip = subparsers.add_parser(
        "roundtrip", help="read an edit list and write it back canonically"
    )
    roundtrip.add_argument("input", help="input JSON path, or - for stdin")
    roundtrip.add_argument("-o", "--output", help="output JSON path (default: stdout)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            _write_or_stdout(_build_demo(), args.output)
        elif args.command == "show":
            _print_markers(_read_or_stdin(args.input))
        elif args.command == "roundtrip":
            _write_or_stdout(_read_or_stdin(args.input), args.output)
        else:
            parser.error(f"unknown command: {args.command}")
    except (MarkerError, OSError) as exc:
        print(f"timeline-markers: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
