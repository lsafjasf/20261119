#!/usr/bin/env python3
"""Regenerate the three committed HEVC test streams."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hevc_tiles.bitstream import rbsp_to_ebsp
from hevc_tiles.parser import parse_stream, split_annex_b


def run(command: list[str], *, env: dict | None = None) -> None:
    subprocess.run(command, check=True, env=env)


def encode(
    kvazaar: str,
    input_yuv: Path,
    output: Path,
    *,
    slices: bool,
    filters: bool,
) -> None:
    command = [
        kvazaar,
        "--preset",
        "medium",
        "-i",
        str(input_yuv),
        "--input-res",
        "256x192",
        "-n",
        "3",
        "--period",
        "1",
        "--gop",
        "0",
        "--tiles",
        "2x2",
        "--no-wpp",
    ]
    if slices:
        command.extend(["--slices", "tiles"])
    if filters:
        command.extend(["--deblock", "0:0", "--sao", "full"])
    else:
        command.extend(["--no-deblock", "--sao", "off"])
    command.extend(
        ["--hash", "none", "--no-info", "-o", str(output)]
    )
    env = os.environ.copy()
    library_dir = (
        Path(kvazaar).resolve().parent.parent / "lib" / "x86_64-linux-gnu"
    )
    if library_dir.is_dir():
        env["LD_LIBRARY_PATH"] = (
            str(library_dir)
            + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        )
    run(command, env=env)


def patch_loop_filter_flag(source: Path, destination: Path) -> None:
    info = parse_stream(source)
    position = info.pps.loop_filter_across_tiles_flag_bit
    if position is None:
        raise RuntimeError("PPS has no loop_filter_across_tiles_enabled_flag")
    original = source.read_bytes()
    nals = split_annex_b(original)
    pps_nal = next(nal for nal in nals if nal.nal_type == 34)
    rbsp = bytearray(pps_nal.rbsp)
    rbsp[position // 8] |= 1 << (7 - position % 8)
    replacement = (
        b"\x00\x00\x00\x01"
        + pps_nal.raw[
            pps_nal.start_code_length : pps_nal.start_code_length + 2
        ]
        + rbsp_to_ebsp(bytes(rbsp))
    )
    output = bytearray()
    cursor = 0
    for nal in nals:
        output.extend(original[cursor : nal.file_offset])
        output.extend(replacement if nal is pps_nal else nal.raw)
        cursor = nal.file_offset + len(nal.raw)
    output.extend(original[cursor:])
    destination.write_bytes(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--kvazaar", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "samples")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="hevc-tiles-sample-") as temp:
        temp_dir = Path(temp)
        input_yuv = temp_dir / "testsrc2.yuv"
        run(
            [
                args.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=256x192:rate=1",
                "-frames:v",
                "3",
                "-pix_fmt",
                "yuv420p",
                "-f",
                "rawvideo",
                str(input_yuv),
                "-y",
            ]
        )
        encode(
            args.kvazaar,
            input_yuv,
            args.output / "tiles_independent.hevc",
            slices=True,
            filters=False,
        )
        encode(
            args.kvazaar,
            input_yuv,
            args.output / "tiles_entrypoints.hevc",
            slices=False,
            filters=False,
        )
        filtered = temp_dir / "filtered.hevc"
        encode(
            args.kvazaar,
            input_yuv,
            filtered,
            slices=True,
            filters=True,
        )
        patch_loop_filter_flag(
            filtered, args.output / "tiles_loopfilter_dependency.hevc"
        )
    print(f"wrote samples to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
