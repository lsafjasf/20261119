from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .ivf import IvfError, read_ivf
from .model import PacketReport
from .parser import inspect_packet


def _header_text(report: PacketReport, subframe_index: int) -> str:
    header = report.subframes[subframe_index].header
    if header is None:
        return "header=INVALID"
    parts = [
        f"profile={header.profile}",
        f"type={header.frame_type_name}",
        f"show_existing={int(header.show_existing_frame)}",
    ]
    if header.show_existing_frame:
        parts.append(f"show_idx={header.frame_to_show_map_idx}")
    else:
        parts.append(f"show_frame={int(bool(header.show_frame))}")
    parts.append(f"display={'yes' if header.displays else 'no'}")
    return " ".join(parts)


def print_report(reports: list[PacketReport]) -> None:
    corrupted = [report.sequence for report in reports if report.corrupted]
    visible = sum(report.visible_count for report in reports)
    superframes = sum(report.is_superframe for report in reports)
    print(
        f"summary: packets={len(reports)} superframes={superframes} "
        f"visible_frames={visible} corrupted_packets={corrupted}"
    )
    for report in reports:
        status = "CORRUPT" if report.corrupted else "ok"
        kind = "superframe" if report.is_superframe else "frame"
        print(
            f"\npacket {report.sequence}: {kind} packet_bytes={report.packet_size} "
            f"subframes={len(report.subframes)} visible={report.visible_count} "
            f"last_presented={report.last_presented_subframe_index} status={status}"
        )
        if report.is_superframe:
            print(
                f"  index: offset={report.index_offset} bytes={report.index_size} "
                f"marker=0x{(report.marker or 0):02x} "
                f"declared_frames={report.declared_frame_count} "
                f"payload_bytes={report.payload_size} sizes={report.sizes} "
                f"sum={report.size_sum}"
            )
        for subframe in report.subframes:
            source = "indexed" if subframe.indexed else "UNINDEXED-SALVAGED"
            print(
                f"  [{subframe.index}] offset={subframe.offset} size={subframe.size} "
                f"source={source} {_header_text(report, subframe.index)}"
            )
        for error in report.errors:
            print(f"  error: {error}")


def reports_as_json(reports: list[PacketReport]) -> dict[str, object]:
    packets = []
    for report in reports:
        item = asdict(report)
        item["visible_count"] = report.visible_count
        item["last_presented_subframe_index"] = report.last_presented_subframe_index
        for subframe in item["subframes"]:
            header = subframe["header"]
            if header is not None:
                header["displays"] = bool(
                    header["show_existing_frame"] or header.get("show_frame")
                )
        packets.append(item)
    return {
        "summary": {
            "packet_count": len(reports),
            "superframe_count": sum(report.is_superframe for report in reports),
            "visible_frame_count": sum(report.visible_count for report in reports),
            "corrupted_packet_sequences": [
                report.sequence for report in reports if report.corrupted
            ],
        },
        "packets": packets,
    }


def extract_subframes(
    reports: list[PacketReport], packets: list[bytes], output_dir: Path
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, object]] = []
    for report, packet in zip(reports, packets):
        payload = packet[: report.payload_size]
        for subframe in report.subframes:
            name = (
                f"packet_{report.sequence:06d}_subframe_{subframe.index:02d}.vp9"
            )
            (output_dir / name).write_bytes(
                payload[subframe.offset : subframe.offset + subframe.size]
            )
            manifest.append(
                {
                    "packet_sequence": report.sequence,
                    "subframe_index": subframe.index,
                    "file": name,
                    "offset": subframe.offset,
                    "size": subframe.size,
                    "indexed": subframe.indexed,
                    "displays": subframe.displays,
                    "packet_corrupted": report.corrupted,
                    "is_last_presented": (
                        subframe.index == report.last_presented_subframe_index
                    ),
                }
            )
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect VP9 superframe indexes in an IVF packet sequence."
    )
    parser.add_argument("input", type=Path, help="input .ivf file")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument(
        "--extract-subframes",
        type=Path,
        metavar="DIR",
        help="write every subframe plus manifest.json",
    )
    parser.add_argument(
        "--fail-on-corrupt",
        action="store_true",
        help="exit with status 2 when any packet has an invalid superframe index",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        _, ivf_packets = read_ivf(args.input)
    except (OSError, IvfError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    reports = [inspect_packet(packet) for packet in ivf_packets]
    if args.extract_subframes:
        extract_subframes(
            reports, [packet.data for packet in ivf_packets], args.extract_subframes
        )
    if args.json:
        print(json.dumps(reports_as_json(reports), indent=2))
    else:
        print_report(reports)

    if args.fail_on_corrupt and any(report.corrupted for report in reports):
        return 2
    return 0
