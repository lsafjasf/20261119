"""Command line / interactive viewer for SEI messages in an H.264 stream."""

import argparse
import sys

from .sei import SeiMessage, sei_type_name
from .stream import AccessUnit, NalUnit, extract_access_units


def _hex_dump(data: bytes, width: int = 16) -> str:
    lines = []
    for offset in range(0, len(data), width):
        chunk = data[offset:offset + width]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"  {offset:08x}  {hex_part:<{width * 3}}  {ascii_part}")
    return "\n".join(lines) if lines else "  (empty)"


def _flatten(access_units: list[AccessUnit]) -> list[tuple[AccessUnit, NalUnit, SeiMessage]]:
    flat = []
    for au in access_units:
        for nal in au.sei_nals:
            for msg in nal.messages:
                flat.append((au, nal, msg))
    return flat


def print_distribution(access_units: list[AccessUnit]) -> None:
    flat = _flatten(access_units)
    counts: dict[str, int] = {}
    au_by_type: dict[str, set[int]] = {}
    for au, _nal, msg in flat:
        name = msg.type_name
        counts[name] = counts.get(name, 0) + 1
        au_by_type.setdefault(name, set()).add(au.index)

    print("== SEI type distribution ==")
    if not flat:
        print("  (no SEI NAL units found)")
        return
    for name in sorted(counts):
        aus = sorted(au_by_type[name])
        au_text = ", ".join(str(i) for i in aus)
        print(f"  {name:<40} {counts[name]:>4} msg   access units: {au_text}")
    print(f"  total SEI messages: {len(flat)}")


def print_access_units(access_units: list[AccessUnit]) -> None:
    print("== Access units ==")
    for au in access_units:
        entries = au.all_sei_messages
        if not entries:
            continue
        kinds = ", ".join(msg.type_name for _nal, msg in entries)
        status = "IDR" if au.is_idr else "non-IDR"
        print(
            f"  AU #{au.index:<4} {status:<8} "
            f"{len(entries)} SEI payload(s): {kinds}"
        )
        for nal_index, (nal, msg) in enumerate(entries):
            flag = "" if msg.parse_ok else " [PARSE ERROR]"
            print(
                f"      - NAL[{nal.index}] type={msg.payload_type} "
                f"({msg.type_name}) size={msg.payload_size}{flag}"
            )


def print_message_detail(
    au: AccessUnit, nal: NalUnit, msg: SeiMessage, show_raw_nal: bool
) -> None:
    print("== SEI payload detail ==")
    print(f"  access unit        : {au.index}")
    print(f"  NAL index          : {nal.index} (offset {nal.stream_offset})")
    print(f"  payload type       : {msg.payload_type} ({msg.type_name})")
    print(f"  declared size      : {msg.payload_size} byte(s)")
    print(f"  parse status       : {'ok' if msg.parse_ok else 'FAILED'}")
    if msg.error:
        print(f"  error              : {msg.error}")
    if nal.sei_warning:
        print(f"  NAL warning        : {nal.sei_warning}")
    print("  parsed fields      :")
    for key, value in msg.fields.items():
        print(f"    {key}: {value}")
    print("  raw payload (RBSP, hex):")
    print(_hex_dump(msg.raw_payload))
    if show_raw_nal:
        print("  raw NAL unit (EBSP incl. header, hex):")
        print(_hex_dump(nal.raw_ebsp))


def interactive(access_units: list[AccessUnit]) -> None:
    flat = _flatten(access_units)
    if not flat:
        print("No SEI messages to inspect.")
        return
    print_distribution(access_units)
    print_access_units(access_units)
    print("\nEnter a message number 0..%d to view its raw payload," % (len(flat) - 1))
    print("'l' to re-list, 'd' for distribution, 'q' to quit.")
    while True:
        try:
            choice = input("sei> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice in ("q", "quit", "exit"):
            return
        if choice in ("l", "list"):
            print_access_units(access_units)
            continue
        if choice in ("d", "dist"):
            print_distribution(access_units)
            continue
        if not choice:
            continue
        if not choice.isdigit() or not 0 <= int(choice) < len(flat):
            print(f"invalid choice; pick 0..{len(flat) - 1}")
            continue
        au, nal, msg = flat[int(choice)]
        print_message_detail(au, nal, msg, show_raw_nal=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="h264_sei",
        description="Extract and inspect SEI NAL units of an H.264 Annex B stream.",
    )
    parser.add_argument("file", help="path to a raw .h264/.264 Annex B stream")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list", help="list access units and SEI messages (default)")
    show = sub.add_parser("show", help="show one message in detail")
    show.add_argument("index", type=int, help="flat message index from 'list'")
    show.add_argument("--raw-nal", action="store_true", help="also dump full NAL bytes")
    sub.add_parser("dist", help="show SEI type distribution only")
    sub.add_parser("interactive", help="browse messages interactively")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        with open(args.file, "rb") as handle:
            stream = handle.read()
    except OSError as exc:
        print(f"cannot read {args.file}: {exc}", file=sys.stderr)
        return 2

    access_units = extract_access_units(stream)
    command = args.command or "list"
    if command == "dist":
        print_distribution(access_units)
    elif command == "show":
        flat = _flatten(access_units)
        if not 0 <= args.index < len(flat):
            print(f"index out of range; only {len(flat)} message(s)", file=sys.stderr)
            return 1
        au, nal, msg = flat[args.index]
        print_message_detail(au, nal, msg, args.raw_nal)
    elif command == "interactive":
        interactive(access_units)
    else:
        print_distribution(access_units)
        print()
        print_access_units(access_units)
        print("\nUse 'show <index>' or 'interactive' to inspect raw payloads.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
