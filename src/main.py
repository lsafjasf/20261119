"""End-to-end driver for the polyphonic F0 estimation experiment.

Pipeline: synthesize material -> estimate per-frame F0 candidates with a
user-adjustable maximum voice count -> evaluate against the independent
ground truth -> render explanation / track figures and save a JSON report.

The estimator only ever reads the rendered WAV; ground truth is consumed
solely by the evaluation and visualization stages.
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import soundfile as sf

from .estimator import estimate_file
from .evaluation import evaluate
from .synth import build_mix
from .visualize import plot_frame_explanation, plot_tracks


def run(max_voices: int, out_dir: str = "results") -> dict:
    wav_path = f"{out_dir}/mix.wav"
    gt_path = f"{out_dir}/ground_truth.json"
    build_mix(wav_path, gt_path)

    audio, fs = sf.read(wav_path)
    results, meta = estimate_file(audio, fs, max_voices=max_voices)

    report = evaluate(results, gt_path)
    report["max_voices"] = max_voices
    report["n_frames"] = len(results)

    # Persist per-frame candidates and confidence.
    frame_dump = [
        {
            "time": round(r.time, 4),
            "candidates": [
                {"f0_hz": round(float(f), 2), "confidence": round(float(c), 4)}
                for f, c in zip(r.f0, r.confidence)
            ],
        }
        for r in results
    ]
    with open(f"{out_dir}/frame_candidates.json", "w", encoding="utf-8") as fh:
        json.dump(frame_dump, fh, indent=2, ensure_ascii=False)
    with open(f"{out_dir}/report.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)

    plot_frame_explanation(
        audio, fs, results,
        times=[1.0, 3.0, 5.0, 6.0],
        out_path=f"{out_dir}/frame_explanation.png",
    )
    plot_tracks(results, gt_path, f"{out_dir}/f0_tracks.png")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Polyphonic F0 experiment")
    parser.add_argument("--max-voices", type=int, default=3, choices=[1, 2, 3],
                        help="maximum number of concurrent voices to track")
    args = parser.parse_args()
    report = run(args.max_voices)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
