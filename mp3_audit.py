#!/usr/bin/env python3
"""Convenience wrapper for running mp3audit without installing the package."""

from mp3audit.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
