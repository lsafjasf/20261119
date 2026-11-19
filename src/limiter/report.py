"""End-to-end runner: process signals, export audio/figures, write report."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from .analysis import (
    ceiling_error,
    deepest_reduction_index,
    detect_hits,
    hit_peak_levels,
    recovery_curve,
    relative_level_spread,
    time_to_recover,
)
from .core import LimiterConfig, PeakLimiter
from .plotting import build_dashboard
from .signals import drum_plus_bass, drum_pulse_train, sustained_bass

MODES = ("bypass", "fixed", "auto")


def _measure(
    limiter: PeakLimiter,
    x: np.ndarray,
    sr: float,
    is_drums: bool,
) -> dict:
    out: dict = {"modes": {}}
    onsets = detect_hits(x, sr) if is_drums else None
    for mode in MODES:
        r = limiter.process(x, mode)
        entry = {
            "true_peak_db": round(r.max_output_peak_db, 3),
            "ceiling_error_db": round(
                ceiling_error(r.max_output_peak_db, limiter.cfg.ceiling_db), 3
            ),
            "clamp_samples": r.clipping_samples,
            "max_gain_reduction_db": round(
                float(20 * np.log10(max(r.gain.min(), 1e-10))), 3
            ),
            "latency_samples": r.latency_samples,
            "latency_ms": round(r.latency_ms, 3),
        }
        if is_drums and onsets is not None:
            levels = hit_peak_levels(r.output, onsets, sr)
            entry["hit_levels_db"] = [round(float(v), 2) for v in levels]
            entry["hit_level_spread_db"] = round(
                relative_level_spread(levels)["spread_db"], 3
            )
            entry["hit_level_stdev_db"] = round(
                relative_level_spread(levels)["stdev_db"], 3
            )
            trig = deepest_reduction_index(r.gain)
            t_ms, red = recovery_curve(r.gain, trig, sr)
            entry["recovery_time_ms"] = round(time_to_recover(red, t_ms), 2)
        else:
            trig = deepest_reduction_index(r.gain)
            t_ms, red = recovery_curve(r.gain, trig, sr)
            entry["reduction_at_100ms_db"] = round(
                float(red[np.argmin(np.abs(t_ms - 100.0))]), 3
            )
            entry["reduction_at_300ms_db"] = round(
                float(red[np.argmin(np.abs(t_ms - 300.0))]), 3
            )
        out["modes"][mode] = entry
    return out


def run(output_dir: str | Path, sr: float = 48_000.0, ceiling_db: float = -1.0) -> dict:
    out = Path(output_dir)
    wav_dir = out / "wav"
    wav_dir.mkdir(parents=True, exist_ok=True)

    cfg = LimiterConfig(sample_rate=sr, ceiling_db=ceiling_db)
    limiter = PeakLimiter(cfg)

    signals = {
        "drum_train": drum_pulse_train(sr),
        "sustained_bass": sustained_bass(sr),
        "drum_plus_bass": drum_plus_bass(sr),
    }

    report: dict = {
        "sample_rate_hz": sr,
        "ceiling_db": ceiling_db,
        "config": {
            "attack_ms": cfg.attack_ms,
            "fixed_release_ms": cfg.release_ms,
            "auto_release_range_ms": [
                cfg.auto_release_min_ms,
                cfg.auto_release_max_ms,
            ],
            "lookahead_ms": cfg.lookahead_ms,
            "oversample_factor": cfg.oversample,
            "fir_taps": cfg.fir_taps,
        },
        "latency_breakdown": {
            "lookahead_samples": limiter._lookahead_base,
            "lookahead_ms": round(
                limiter._lookahead_base / sr * 1000.0, 3
            ),
            "sidechain_fir_group_delay_over_ticks": limiter._fir_group_delay,
            "sidechain_fir_group_delay_ms": round(
                limiter._fir_group_delay / (sr * cfg.oversample) * 1000.0, 3
            ),
            "audio_path_total_ms": round(limiter.latency_ms, 3),
            "bypass_latency_ms": 0.0,
        },
        "signals": {},
    }

    for name, x in signals.items():
        sf.write(wav_dir / f"{name}_input.wav", x.astype(np.float32), int(sr))
        is_drums = name != "sustained_bass"
        report["signals"][name] = _measure(limiter, x, sr, is_drums)
        for mode in MODES:
            res = limiter.process(x, mode)
            sf.write(
                wav_dir / f"{name}_{mode}.wav",
                res.output.astype(np.float32),
                int(sr),
            )

    fig = build_dashboard(
        signals["drum_train"], signals["sustained_bass"], sr, limiter
    )
    fig.savefig(out / "dashboard.png", dpi=140)
    fig.savefig(out / "dashboard.pdf")
    import matplotlib.pyplot as plt

    plt.close(fig)

    with open(out / "report.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    return report
