"""Order/window sweeps and stability audits."""

from __future__ import annotations

import math
import shutil
import tempfile
import time
from pathlib import Path
from typing import Sequence

from .codec import encode_file
from .dsp import AnalysisConfig, rms, snr_db
from .wavio import read_wav

PROBE_LABELS = {
    "vowel": "持续元音 /a/",
    "fricative": "无声摩擦音 /s/",
    "silence": "数字静音（全零）",
}


def _frame_detail(analysis: dict) -> dict:
    frames = analysis["frames"]
    if not frames:
        return {}
    selected = max(frames, key=lambda frame: frame["frame_rms"])
    return {
        "frame_index": selected["index"],
        "frame_start": selected["start"],
        "frame_samples": selected["frame_samples"],
        "prediction_error": selected["prediction_error"],
        "autocorrelation_matrix": selected["autocorrelation_matrix"],
        "autocorrelation": selected["autocorrelation"],
        "predictor": selected["predictor"],
        "reflection": selected["reflection"],
        "poles": selected["poles"],
        "pole_radius": selected["pole_radius"],
        "stable": selected["stable"],
        "prediction_gain_db": selected["prediction_gain_db"],
        "residual_rms": selected["residual_rms"],
    }


def _error_metrics(reference: Sequence[float], estimate: Sequence[float]) -> dict:
    count = min(len(reference), len(estimate))
    errors = [reference[index] - estimate[index] for index in range(count)]
    return {
        "snr_db": snr_db(reference, estimate),
        "mse": sum(value * value for value in errors) / count if count else 0.0,
        "max_abs_error": max((abs(value) for value in errors), default=0.0),
        "error_rms": rms(errors),
    }


def _run_one(
    wav_path: Path,
    signal_name: str,
    sample_rate: int,
    sample_count: int,
    order: int,
    window_length: int,
    frame_length: int,
    coefficient_mode: str,
    coefficient_bits: int,
    residual_bits: int,
    workspace: Path,
) -> dict:
    config = AnalysisConfig(
        sample_rate=sample_rate,
        frame_length=frame_length,
        hop_length=frame_length,
        window_length=window_length,
        order=order,
        window_type="hann",
        preemphasis=0.0,
    )
    prefix = workspace / f"{signal_name}_o{order}_w{window_length}_{coefficient_mode}"
    encoded = encode_file(
        wav_path,
        prefix,
        config,
        coefficient_mode=coefficient_mode,
        coefficient_bits=coefficient_bits,
        residual_bits=residual_bits,
    )
    reference, _ = read_wav(wav_path)
    metrics = _error_metrics(reference, encoded.reconstruction)
    detail = _frame_detail(encoded.analysis)
    detail["prediction_error_energy"] = sum(
        value * value for value in detail.get("prediction_error", [])
    )
    return {
        "signal": signal_name,
        "label": PROBE_LABELS.get(signal_name, signal_name),
        "order": order,
        "window_length": window_length,
        "frame_length": frame_length,
        "coefficient_mode": coefficient_mode,
        "coefficient_bits": coefficient_bits,
        "residual_bits": residual_bits,
        "sample_count": sample_count,
        "scale_samples_per_second": sample_rate,
        "snr_db": metrics["snr_db"],
        "mse": metrics["mse"],
        "max_abs_error": metrics["max_abs_error"],
        "error_rms": metrics["error_rms"],
        "reconstruction_peak": max(
            (abs(value) for value in encoded.reconstruction), default=0.0
        ),
        **encoded.sizes,
        "stability": encoded.stability,
        "detail": detail,
        "_prefix": str(prefix),
    }


def run_sweep(
    inputs: dict[str, Path],
    orders: Sequence[int],
    windows: Sequence[int],
    output_dir: str | Path,
    frame_length: int = 400,
    coefficient_bits: int = 8,
    residual_bits: int = 8,
) -> dict:
    """Run all order/window combinations and retain useful stream files."""

    destination = Path(output_dir)
    stream_dir = destination / "streams"
    stream_dir.mkdir(parents=True, exist_ok=True)
    stream_window = 320 if 320 in windows else max(windows)
    results: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="lpc_sweep_") as temporary:
        workspace = Path(temporary)
        for signal_name, wav_path in inputs.items():
            samples, sample_rate = read_wav(wav_path)
            for window_length in windows:
                for order in orders:
                    if order >= window_length:
                        continue
                    record = _run_one(
                        wav_path,
                        signal_name,
                        sample_rate,
                        len(samples),
                        order,
                        window_length,
                        frame_length,
                        "rc",
                        coefficient_bits,
                        residual_bits,
                        workspace,
                    )
                    prefix = Path(record.pop("_prefix"))
                    if window_length == stream_window:
                        target = stream_dir / f"{signal_name}_o{order}"
                        shutil.copy2(prefix.with_suffix(".coef"), target.with_suffix(".coef"))
                        shutil.copy2(prefix.with_suffix(".res"), target.with_suffix(".res"))
                        shutil.copy2(
                            prefix.with_suffix(".recon.wav"),
                            target.with_suffix(".recon.wav"),
                        )
                        record["stream_prefix"] = str(
                            target.relative_to(destination)
                        )
                    results.append(record)

    return {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "frame_length": frame_length,
        "coefficient_bits": coefficient_bits,
        "residual_bits": residual_bits,
        "orders": list(orders),
        "windows": list(windows),
        "results": results,
    }


def run_stability_audit(
    inputs: dict[str, Path],
    frame_length: int = 400,
    window_length: int = 320,
) -> dict:
    """Exercise silence and aggressive direct-coefficient quantization."""

    audits: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="lpc_audit_") as temporary:
        workspace = Path(temporary)
        for signal_name, wav_path in inputs.items():
            samples, sample_rate = read_wav(wav_path)
            for mode, bits in (("rc", 8), ("direct", 4)):
                order = min(16, window_length - 1)
                record = _run_one(
                    wav_path,
                    signal_name,
                    sample_rate,
                    len(samples),
                    order,
                    window_length,
                    frame_length,
                    mode,
                    bits,
                    8,
                    workspace,
                )
                finite = all(math.isfinite(value) for value in record.get("detail", {}).get("predictor", []))
                audits.append(
                    {
                        "signal": signal_name,
                        "label": PROBE_LABELS.get(signal_name, signal_name),
                        "mode": mode,
                        "coefficient_bits": bits,
                        "order": order,
                        "window_length": window_length,
                        "snr_db": record["snr_db"],
                        "mse": record["mse"],
                        "silence_frames": record["stability"]["silence_frames"],
                        "unstable_detected": record["stability"]["unstable_detected"],
                        "stabilized": record["stability"]["stabilized"],
                        "rejected": record["stability"]["rejected"],
                        "max_quantized_pole_radius": record["stability"][
                            "max_quantized_pole_radius"
                        ],
                        "max_nan_or_inf": record["stability"]["max_nan_or_inf"],
                        "methods": record["stability"]["methods"],
                        "finite_predictor": finite,
                        "silence_output_peak": (
                            record["reconstruction_peak"]
                            if signal_name == "silence"
                            else None
                        ),
                    }
                )
    return {
        "policy": {
            "rc": "Reflection coefficients are quantized inside (-1, 1); stability is inherent, with a numeric shrink fallback.",
            "direct": "Predictor coefficients are pole-checked. Unstable frames are shrunk by 0.95 until stable; if that fails they are rejected to a zero predictor.",
        },
        "audits": audits,
    }


def build_demo(
    sample_dir: str | Path,
    artifact_dir: str | Path,
    orders: Sequence[int] = (4, 6, 8, 10, 12, 16),
    windows: Sequence[int] = (160, 240, 320),
    frame_length: int = 400,
) -> dict:
    from .signals import write_probes

    samples = write_probes(sample_dir)
    destination = Path(artifact_dir)
    destination.mkdir(parents=True, exist_ok=True)
    sweep = run_sweep(
        samples,
        orders,
        windows,
        destination,
        frame_length=frame_length,
        coefficient_bits=8,
        residual_bits=8,
    )
    sweep["probes"] = {
        name: {
            "label": PROBE_LABELS[name],
            "path": str(Path(sample_dir) / f"{name}.wav"),
        }
        for name in samples
    }
    sweep["stability_audit"] = run_stability_audit(
        samples, frame_length=frame_length, window_length=320
    )
    return sweep
