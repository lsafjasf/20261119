#!/usr/bin/env python3
"""Regenerate checked-in MP3 fixtures used by the test suite."""

from __future__ import annotations

from pathlib import Path

from mp3_samples import (
    cbr_stream,
    drift_stream,
    false_sync_stream,
    id3_stream,
    prefill_stream,
    semantic_damage_stream,
    vbr_stream,
)


SAMPLES = {
    "cbr_128k.mp3": cbr_stream,
    "vbr.mp3": vbr_stream,
    "id3_cbr.mp3": id3_stream,
    "prefill.mp3": prefill_stream,
    "drift.mp3": drift_stream,
    "false_sync.mp3": false_sync_stream,
    "semantic_damage.mp3": semantic_damage_stream,
}


def main() -> None:
    output = Path(__file__).resolve().parent.parent / "samples"
    output.mkdir(exist_ok=True)
    for name, factory in SAMPLES.items():
        (output / name).write_bytes(factory().data)
        print(f"{name}: {(output / name).stat().st_size} bytes")


if __name__ == "__main__":
    main()
