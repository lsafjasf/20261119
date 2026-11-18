"""Module entry point supporting ``python -m mp4faststart``."""

from __future__ import annotations

import sys

from .cli import faststart_main, verify_main


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "verify":
        return verify_main(sys.argv[2:])
    return faststart_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
