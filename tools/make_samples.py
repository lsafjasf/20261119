#!/usr/bin/env python3
"""Regenerate the checked-in FLAC corruption fixtures.

This script needs the reference `flac` encoder. The inspector itself only
uses the Python standard library.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flac_inspect import inspect_file  # noqa: E402


def _write_source_wav(path: Path) -> None:
    sample_rate = 44100
    block_lengths = (1024, 1024, 1024, 904)
    rng = random.Random(0xF1AC)
    phase = 0.0
    frames = bytearray()

    for block_index, count in enumerate(block_lengths):
        for index in range(count):
            if block_index == 0:
                left = right = 0
            elif block_index == 1:
                left = rng.randrange(-32768, 32768)
                right = rng.randrange(-32768, 32768)
            elif block_index == 2:
                left = int(21000 * math.sin(phase) + 3000 * math.sin(phase * 3.1))
                right = int(17000 * math.sin(phase * 1.013) + 2500 * math.sin(phase * 2.7))
                phase += 2 * math.pi * 440 / sample_rate
            else:
                value = ((index * 97 + block_index * 31) % 4001) - 2000
                left, right = value, -value
            frames.extend(struct.pack("<hh", left, right))

    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(frames)


def _find_flac(explicit: str | None) -> str:
    candidate = explicit or os.environ.get("FLAC") or shutil.which("flac")
    if not candidate:
        raise SystemExit(
            "flac encoder not found; pass --flac /path/to/flac or set FLAC"
        )
    return candidate


def _encode(flac_binary: str, wav_path: Path, flac_path: Path) -> None:
    subprocess.run(
        [
            flac_binary,
            "--silent",
            "--force",
            "--blocksize=1024",
            "-8",
            "--no-padding",
            "-o",
            str(flac_path),
            str(wav_path),
        ],
        check=True,
    )


def _write_marker(data: bytearray, offset: int) -> None:
    # Deliberately sync-like but structurally impossible: reserved block-size
    # and sample-rate codes are both set.
    data[offset : offset + 4] = b"\xff\xf8\x0f\x00"


def _make_variants(valid_path: Path, sample_dir: Path) -> None:
    inspection = inspect_file(valid_path)
    target = inspection.frames[1]
    if target.header_size is None or target.size is None:
        raise RuntimeError("reference frame 1 did not parse")
    body = target.offset + target.header_size

    header = bytearray(valid_path.read_bytes())
    header[target.offset + 3] ^= 0x01
    _write_marker(header, body + 200)
    (sample_dir / "header_corrupt.flac").write_bytes(header)

    payload = bytearray(valid_path.read_bytes())
    payload[body + 100] ^= 0x80
    _write_marker(payload, body + 300)
    (sample_dir / "payload_corrupt.flac").write_bytes(payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--flac", help="path to the flac encoder")
    parser.add_argument(
        "--out", type=Path, default=ROOT / "samples", help="fixture directory"
    )
    args = parser.parse_args()

    flac_binary = _find_flac(args.flac)
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        wav_path = Path(temporary) / "source.wav"
        valid_path = args.out / "valid.flac"
        _write_source_wav(wav_path)
        _encode(flac_binary, wav_path, valid_path)
    _make_variants(valid_path, args.out)
    print(f"wrote {valid_path}, header_corrupt.flac, payload_corrupt.flac")


if __name__ == "__main__":
    main()
