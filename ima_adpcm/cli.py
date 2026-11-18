"""Command-line interface for the IMA ADPCM codec."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .codec import (
    CodecError,
    block_offset,
    decode_block_at,
    decode_file,
    encode_file,
    parse_block_header,
    parse_file_header,
)
from .reference import ReferenceError, verify_against_reference
from .wav_io import (
    PcmFormatError,
    read_raw_pcm16,
    read_wav,
    write_raw_pcm16,
    write_wav_pcm16,
)


def _add_raw_input_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--raw", action="store_true", help="input is headerless PCM16")
    parser.add_argument("--channels", type=int, default=1)
    parser.add_argument("--sample-rate", type=int, default=8000)


def _load_frames(args: argparse.Namespace):
    data = Path(args.input).read_bytes()
    if args.raw:
        frames = read_raw_pcm16(data, channels=args.channels)
        return frames, args.sample_rate, args.channels
    wav = read_wav(data)
    return wav.frames, wav.sample_rate, wav.channels


def _cmd_encode(args: argparse.Namespace) -> int:
    frames, sample_rate, channels = _load_frames(args)
    encoded = encode_file(
        frames,
        sample_rate=sample_rate,
        frames_per_block=args.frames_per_block,
    )
    Path(args.output).write_bytes(encoded)
    frame_count = len(frames[0]) if frames else 0
    print(
        f"encoded {frame_count} frames, {channels} channel(s), "
        f"{sample_rate} Hz -> {args.output} ({len(encoded)} bytes)"
    )
    return 0


def _cmd_decode(args: argparse.Namespace) -> int:
    encoded = Path(args.input).read_bytes()
    audio = decode_file(encoded, strict=not args.salvage)
    if args.raw:
        output = write_raw_pcm16(audio.frames)
    else:
        output = write_wav_pcm16(audio.sample_rate, audio.channels, audio.frames)
    Path(args.output).write_bytes(output)
    action = "salvaged" if args.salvage else "decoded"
    print(
        f"{action} {audio.frame_count} frames, {audio.channels} channel(s), "
        f"{audio.sample_rate} Hz -> {args.output} ({len(output)} bytes)"
    )
    for failure in audio.failures:
        print(
            f"warning: block {failure.block_index} frames "
            f"{failure.frame_start}..{failure.frame_start + failure.frame_count - 1}: "
            f"{failure.message}",
            file=sys.stderr,
        )
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    encoded = Path(args.input).read_bytes()
    header = parse_file_header(encoded)
    print(
        f"container: {header.channels} channel(s), {header.sample_rate} Hz, "
        f"{header.total_frames} frames, {header.block_count} block(s), "
        f"{header.frames_per_block} frames/full block"
    )
    print(f"file header: {header.header_size} bytes; full block: {header.full_block_size} bytes")
    if args.block is None:
        for index in range(header.block_count):
            offset = block_offset(header, index)
            try:
                block = parse_block_header(encoded, offset=offset)
                detail = (
                    f"frames={block.frame_count} payload={block.payload_size} "
                    f"bytes={block.total_size}"
                )
            except CodecError as error:
                detail = f"INVALID: {error}"
            print(f"  block {index:4d} offset={offset:8d} {detail}")
        return 0

    decoded = decode_block_at(
        encoded,
        args.block,
        verify_crc=True,
        collect_trace=args.trace,
    )
    print(
        f"block {args.block}: frames=0..{decoded.frame_count - 1}, "
        f"payload={decoded.payload_size} bytes"
    )
    for channel, state in enumerate(decoded.initial_states):
        print(
            f"  header ch{channel}: predictor={state.predictor} "
            f"index={state.step_index} step={state.step_size}"
        )
    if args.trace:
        for event in decoded.trace[: args.limit]:
            print(
                f"  frame={event.frame:5d} ch={event.channel} "
                f"code={event.code:x} pred={event.prediction} "
                f"qdiff={event.quantized_difference} "
                f"recon={event.reconstructed} "
                f"index={event.step_index_before}->{event.step_index_after}"
            )
        if len(decoded.trace) > args.limit:
            print(f"  ... {len(decoded.trace) - args.limit} more trace rows")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    encoded = Path(args.input).read_bytes()
    header = parse_file_header(encoded)
    verified = 0
    for index in range(header.block_count):
        offset = block_offset(header, index)
        verified += verify_against_reference(encoded, offset=offset)
        print(f"block {index}: reference matches ({offset} byte offset)")
    print(f"verified {verified} samples in {header.block_count} block(s)")
    return 0


def _cmd_demo(_args: argparse.Namespace) -> int:
    from .demo import main as demo_main

    return demo_main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ima-adpcm",
        description="IMA ADPCM codec with independent, random-access blocks",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    encode_parser = subparsers.add_parser("encode", help="encode WAV/raw PCM to IADP")
    encode_parser.add_argument("input")
    encode_parser.add_argument("output")
    encode_parser.add_argument("--frames-per-block", type=int, default=256)
    _add_raw_input_options(encode_parser)
    encode_parser.set_defaults(func=_cmd_encode)

    decode_parser = subparsers.add_parser("decode", help="decode IADP blocks")
    decode_parser.add_argument("input")
    decode_parser.add_argument("output")
    decode_parser.add_argument("--raw", action="store_true", help="write headerless PCM16")
    decode_parser.add_argument(
        "--salvage",
        action="store_true",
        help="replace corrupt blocks with silence and continue",
    )
    decode_parser.set_defaults(func=_cmd_decode)

    inspect_parser = subparsers.add_parser("inspect", help="show block layout or trace")
    inspect_parser.add_argument("input")
    inspect_parser.add_argument("--block", type=int)
    inspect_parser.add_argument("--trace", action="store_true")
    inspect_parser.add_argument("--limit", type=int, default=40)
    inspect_parser.set_defaults(func=_cmd_inspect)

    verify_parser = subparsers.add_parser("verify", help="verify with independent decoder")
    verify_parser.add_argument("input")
    verify_parser.set_defaults(func=_cmd_verify)

    demo_parser = subparsers.add_parser("demo", help="run all demonstrations")
    demo_parser.set_defaults(func=_cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (CodecError, PcmFormatError, ReferenceError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
