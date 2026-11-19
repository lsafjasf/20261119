"""Command-line entry point for headless synthesis and export."""

from __future__ import annotations

import argparse
from pathlib import Path

from .exporters import render, write_trajectory_csv, write_trajectory_json, write_wav
from .params import SAMPLE_RATE, VOWEL_PRESETS


def main(argv=None):
    parser = argparse.ArgumentParser(description="Glottal formant vowel synthesizer")
    parser.add_argument("--preset", default="a", choices=sorted(VOWEL_PRESETS))
    parser.add_argument("--to-preset", default=None, choices=sorted(VOWEL_PRESETS),
                        help="Optional target vowel for a timed transition")
    parser.add_argument("--duration", type=float, default=2.0)
    parser.add_argument("--f0", type=float, default=None)
    parser.add_argument("--breathiness", type=float, default=None)
    parser.add_argument("--outdir", default="artifacts")
    parser.add_argument("--fs", type=int, default=SAMPLE_RATE)
    args = parser.parse_args(argv)

    start = VOWEL_PRESETS[args.preset]
    overrides = {}
    if args.f0 is not None:
        overrides["f0"] = args.f0
    if args.breathiness is not None:
        overrides["breathiness"] = args.breathiness
    start = start.updated(**overrides)

    if args.to_preset:
        end = VOWEL_PRESETS[args.to_preset].updated(**overrides)
        target = [(0.0, start), (args.duration, end)]
        label = f"{args.preset}_to_{args.to_preset}"
    else:
        target = start
        label = args.preset

    audio, trajectory = render(target, duration=args.duration, fs=args.fs)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    wav = outdir / f"{label}.wav"
    csv_path = outdir / f"{label}_trajectory.csv"
    json_path = outdir / f"{label}_trajectory.json"
    write_wav(str(wav), audio, args.fs)
    write_trajectory_csv(str(csv_path), trajectory)
    write_trajectory_json(str(json_path), trajectory,
                          {"fs": args.fs, "duration": args.duration})
    print(f"Wrote {wav} ({audio.shape[0]} samples)")
    print(f"Wrote {csv_path} and {json_path} ({len(trajectory)} control blocks)")


if __name__ == "__main__":
    main()
