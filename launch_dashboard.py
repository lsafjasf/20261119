#!/usr/bin/env python3
"""Launch the interactive tuning dashboard for a chosen scenario."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from afs.dashboard import launch_interactive
from afs.scenarios import SCENARIOS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=sorted(SCENARIOS),
        default="drifting_howl",
    )
    args = parser.parse_args()
    launch_interactive(args.scenario)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
