#!/usr/bin/env python3
"""Regenerate the checked-in legal and truncated-index IVF samples."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vp9_superframe.ivf import read_ivf, write_ivf  # noqa: E402
from vp9_superframe.parser import inspect_ivf, inspect_packet  # noqa: E402

WIDTH = 160
HEIGHT = 120
FRAME_COUNT = 4


def _local_tool_env() -> dict[str, str]:
    env = os.environ.copy()
    library_dirs = [
        ROOT / ".tools/root/usr/lib/x86_64-linux-gnu",
        ROOT / ".tools/root/usr/lib/aarch64-linux-gnu",
    ]
    existing = env.get("LD_LIBRARY_PATH")
    paths = [str(path) for path in library_dirs if path.is_dir()]
    if existing:
        paths.append(existing)
    if paths:
        env["LD_LIBRARY_PATH"] = ":".join(paths)
    return env


def _find_vpxenc() -> Path:
    candidates = [
        os.environ.get("VPXENC"),
        str(ROOT / ".tools/root/usr/bin/vpxenc"),
        shutil.which("vpxenc"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise RuntimeError(
        "vpxenc not found; set VPXENC or install vpx-tools. "
        "Checked-in samples can still be inspected without vpxenc."
    )


def _write_raw_video(path: Path) -> None:
    raw = bytearray()
    chroma_w, chroma_h = WIDTH // 2, HEIGHT // 2
    for frame in range(FRAME_COUNT):
        for y in range(HEIGHT):
            for x in range(WIDTH):
                raw.append((x * 2 + y + frame * 11) & 0xFF)
        for y in range(chroma_h):
            for x in range(chroma_w):
                raw.append((96 + x * 3 + frame * 7) & 0xFF)
        for y in range(chroma_h):
            for x in range(chroma_w):
                raw.append((160 + y * 3 + frame * 5) & 0xFF)
    path.write_bytes(raw)


def _encode_legal(output: Path, work_dir: Path) -> None:
    raw_path = work_dir / "source.yuv"
    stats_path = work_dir / "vpxenc.stats"
    _write_raw_video(raw_path)
    cmd = [
        str(_find_vpxenc()),
        "--codec=vp9",
        "--ivf",
        f"--limit={FRAME_COUNT}",
        f"--width={WIDTH}",
        f"--height={HEIGHT}",
        "--fps=30/1",
        "--passes=2",
        "--good",
        "--cpu-used=2",
        "--lag-in-frames=12",
        "--auto-alt-ref=1",
        "--arnr-maxframes=7",
        "--kf-max-dist=999",
        f"--fpf={stats_path}",
        "--quiet",
        f"--output={output}",
        str(raw_path),
    ]
    subprocess.run(cmd, check=True, env=_local_tool_env())


def _truncate_first_superframe_index(source: Path, output: Path) -> int:
    header, packets = read_ivf(source)
    reports = [inspect_packet(packet) for packet in packets]
    target = next(
        (
            report
            for report in reports
            if report.is_superframe
            and not report.corrupted
            and len(report.sizes) >= 2
            and report.index_offset is not None
        ),
        None,
    )
    if target is None:
        raise RuntimeError("encoder output does not contain a 2+ frame superframe")

    assert target.marker is not None and target.index_offset is not None
    magnitude = ((target.marker >> 3) & 0x03) + 1
    one_frame_marker = 0xC0 | ((magnitude - 1) << 3)
    shortened_index = (
        bytes([one_frame_marker])
        + target.sizes[0].to_bytes(magnitude, "little")
        + bytes([one_frame_marker])
    )
    raw_packet = packets[target.sequence].data
    mutated = raw_packet[: target.index_offset] + shortened_index
    if len(mutated) != target.payload_size + len(shortened_index):
        raise RuntimeError("unexpected bytes while shortening the superframe index")

    replacement = [
        (packet.timestamp, bytes(mutated) if packet.sequence == target.sequence else packet.data)
        for packet in packets
    ]
    write_ivf(output, header, replacement)
    return target.sequence


def main() -> int:
    samples_dir = ROOT / "samples"
    samples_dir.mkdir(exist_ok=True)
    legal_path = samples_dir / "legal.ivf"
    truncated_path = samples_dir / "truncated-index.ivf"

    with tempfile.TemporaryDirectory(prefix="vp9-superframe-") as temp:
        _encode_legal(legal_path, Path(temp))

    corrupted_sequence = _truncate_first_superframe_index(legal_path, truncated_path)
    _, legal_reports = inspect_ivf(legal_path)
    _, truncated_reports = inspect_ivf(truncated_path)

    print(f"wrote {legal_path.relative_to(ROOT)}")
    print(
        f"  visible={sum(r.visible_count for r in legal_reports)} "
        f"superframes={sum(r.is_superframe for r in legal_reports)} "
        f"corrupted={[r.sequence for r in legal_reports if r.corrupted]}"
    )
    print(f"wrote {truncated_path.relative_to(ROOT)}")
    print(
        f"  visible={sum(r.visible_count for r in truncated_reports)} "
        f"corrupted_packet={corrupted_sequence} "
        f"salvaged_tail={any(not s.indexed for r in truncated_reports for s in r.subframes)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
