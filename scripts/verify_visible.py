#!/usr/bin/env python3
"""Compare parser-visible frames with libvpx's vpxdec output count."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vp9_superframe.parser import inspect_ivf  # noqa: E402

SUMMARY_RE = re.compile(
    r"(?P<decoded>\d+) decoded frames/(?P<showed>\d+) showed frames"
)


def _find_vpxdec() -> Path:
    candidates = [
        os.environ.get("VPXDEC"),
        str(ROOT / ".tools/root/usr/bin/vpxdec"),
        shutil.which("vpxdec"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise RuntimeError("vpxdec not found; set VPXDEC or install vpx-tools")


def _environment() -> dict[str, str]:
    env = os.environ.copy()
    library_dirs = [
        ROOT / ".tools/root/usr/lib/x86_64-linux-gnu",
        ROOT / ".tools/root/usr/lib/aarch64-linux-gnu",
    ]
    paths = [str(path) for path in library_dirs if path.is_dir()]
    if env.get("LD_LIBRARY_PATH"):
        paths.append(env["LD_LIBRARY_PATH"])
    if paths:
        env["LD_LIBRARY_PATH"] = ":".join(paths)
    return env


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument(
        "--expect-dropped-tail",
        action="store_true",
        help="expect parsed visible frames to exceed vpxdec's showed frames",
    )
    args = parser.parse_args()

    _, reports = inspect_ivf(args.input)
    parser_visible = sum(report.visible_count for report in reports)
    corrupted = [report.sequence for report in reports if report.corrupted]
    result = subprocess.run(
        [
            str(_find_vpxdec()),
            "--codec=vp9",
            "--summary",
            "--noblit",
            str(args.input),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=_environment(),
        check=False,
    )
    match = SUMMARY_RE.search(result.stdout)
    print(f"parser_visible_frames={parser_visible}")
    print(f"parser_corrupted_packets={corrupted}")
    print(f"vpxdec_exit={result.returncode}")
    if match:
        print(f"vpxdec_decoded_frames={match.group('decoded')}")
        print(f"vpxdec_showed_frames={match.group('showed')}")
    else:
        print("vpxdec_summary=absent")
        for line in result.stdout.strip().splitlines():
            print(f"vpxdec: {line}")

    if args.expect_dropped_tail:
        if match is None or not corrupted:
            return 1
        return 0 if parser_visible > int(match.group("showed")) else 1
    if match is None:
        return 1
    return 0 if int(match.group("showed")) == parser_visible else 1


if __name__ == "__main__":
    raise SystemExit(main())
