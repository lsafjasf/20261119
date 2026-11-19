"""Reporting helpers: latency conversion, error metrics, MAC summaries."""

from __future__ import annotations

import math


def latency_ms(latency_samples: int, sample_rate: float) -> float:
    return 1000.0 * latency_samples / sample_rate


def error_metrics(reference: list[float], test: list[float]) -> dict[str, float]:
    n = min(len(reference), len(test))
    if n == 0:
        return {"max_abs": 0.0, "rms": 0.0,
                "snr_db": math.inf, "length_ref": len(reference),
                "length_test": len(test)}
    errs = [test[i] - reference[i] for i in range(n)]
    sig_power = sum(v * v for v in reference[:n]) / n
    err_power = sum(e * e for e in errs) / n
    max_abs = max(abs(e) for e in errs)
    rms = math.sqrt(err_power)
    snr = math.inf if err_power == 0 else 10 * math.log10(
        sig_power / err_power) if sig_power > 0 else -math.inf
    return {
        "max_abs": max_abs,
        "rms": rms,
        "snr_db": snr,
        "length_ref": len(reference),
        "length_test": len(test),
    }


def fmt_macs(complex_macs: int, real_macs: int | None = None) -> str:
    """A complex MAC is roughly 4 real multiplies + 2 real adds."""
    real_equiv = complex_macs * 4
    if real_macs is not None:
        ratio = real_macs / complex_macs if complex_macs else math.inf
        return (f"{complex_macs:,} complex MACs "
                f"(~{real_equiv:,} real-equiv); direct: {real_macs:,} "
                f"real MACs -> {ratio:.1f}x fewer multiply-accumulates")
    return f"{complex_macs:,} complex MACs (~{real_equiv:,} real-equiv)"
