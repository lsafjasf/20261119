#!/usr/bin/env python3
"""Command-line entry point for the auto-release peak limiter.

Examples
--------
Run the full synthetic benchmark and write every artifact::

    python3 run_limiter.py --out artifacts/run

Process a real file in one audition mode::

    python3 run_limiter.py --input my_drums.wav --mode auto --out out/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).parent / "src"))

from limiter import LimiterConfig, PeakLimiter  # noqa: E402
from limiter.report import run  # noqa: E402


def process_file(input_path: Path, mode: str, out_dir: Path, ceiling_db: float) -> None:
    data, sr = sf.read(input_path, always_2d=True)
    mono = data.mean(axis=1)
    cfg = LimiterConfig(sample_rate=float(sr), ceiling_db=ceiling_db)
    limiter = PeakLimiter(cfg)
    result = limiter.process(mono, mode)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{input_path.stem}_{mode}.wav"
    sf.write(out_path, result.output.astype(np.float32), sr)
    print(f"mode={mode} latency={result.latency_ms:.2f} ms "
          f"true_peak={result.max_output_peak_db:.2f} dB "
          f"clamps={result.clipping_samples} -> {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-release peak limiter")
    parser.add_argument("--input", type=Path, help="optional input WAV file")
    parser.add_argument(
        "--mode",
        choices=("bypass", "fixed", "auto"),
        default="auto",
        help="audition mode when processing --input",
    )
    parser.add_argument("--out", type=Path, default=Path("artifacts/run"))
    parser.add_argument("--ceiling-db", type=float, default=-1.0)
    parser.add_argument("--sr", type=float, default=48_000.0)
    args = parser.parse_args()

    if args.input:
        process_file(args.input, args.mode, args.out, args.ceiling_db)
        return

    report = run(args.out, sr=args.sr, ceiling_db=args.ceiling_db)
    print(f"Artifacts written to {args.out}")
    print(
        "Audio-path latency: "
        f"{report['latency_breakdown']['audio_path_total_ms']} ms "
        f"({report['latency_breakdown']['lookahead_samples']} samples); "
        "bypass = 0 ms"
    )


if __name__ == "__main__":
    main()
