"""Command line interface: inspect ADTS streams and export segment indexes."""

import argparse
import sys
from pathlib import Path

from .parser import parse_stream


def _print_human(index) -> None:
    print(f"source={index.source} size={index.file_size} bytes")
    print(f"segments={len(index.segments)}")
    for seg in index.segments:
        id3_note = "no-id3" if seg.id3 is None else f"id3@{seg.id3.offset}+{seg.id3.total_size}"
        print(
            f"  [{seg.index}] bytes {seg.start}..{seg.end} "
            f"(playable {seg.start}..{seg.playable_end}) {id3_note}"
        )
        print(
            f"      frames={seg.frame_count} profile={seg.profile} "
            f"(AOT {seg.audio_object_type}) "
            f"sr_index={seg.sample_rate_index} sample_rate={seg.sample_rate} Hz "
            f"channels={seg.channel_config}"
        )
        print(
            f"      samples={seg.sample_count} "
            f"duration={seg.duration_seconds:.6f}s"
        )
        if seg.truncated is not None:
            item = seg.truncated
            print(
                f"      TRUNCATED at offset {item.offset}: {item.reason} "
                f"(available={item.available}, expected={item.expected_length})"
            )
    for item in index.truncated:
        print(
            f"  trunc-region offset={item.offset} available={item.available} "
            f"expected={item.expected_length}: {item.reason}",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="adts-parse",
        description="Parse ADTS/AAC streams, validate headers and segment concatenations.",
    )
    parser.add_argument("path", type=Path, help="path to the ADTS (.aac/.adts) file")
    parser.add_argument("--json", dest="json_out", type=Path, help="write segment index JSON")
    parser.add_argument(
        "--no-param-split",
        action="store_true",
        help="do not split segments when sample rate/profile changes between frames",
    )
    args = parser.parse_args(argv)

    data = args.path.read_bytes()
    index = parse_stream(
        data, source=str(args.path), split_on_param_change=not args.no_param_split
    )
    _print_human(index)

    if args.json_out is not None:
        args.json_out.write_text(index.to_json() + "\n", encoding="utf-8")
        print(f"index written to {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
