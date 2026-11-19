"""Measurement helpers: hit levels, recovery curves, ceiling error."""

from __future__ import annotations

import numpy as np


def db(x: np.ndarray | float) -> np.ndarray | float:
    return 20.0 * np.log10(np.maximum(np.abs(x), 1e-10))


def detect_hits(
    x: np.ndarray, sr: float, hit_hz: float = 4.0, min_separation_ms: float = 100.0
) -> np.ndarray:
    """Return onset sample indices of drum hits via local maxima."""
    env = np.maximum(x, 0.0)
    # Simple peak picking on the rectified signal.
    sep = int(round(min_separation_ms / 1000.0 * sr))
    period = int(round(sr / hit_hz))
    candidates = []
    n = len(x)
    for center in range(period // 2, n - sep, period):
        lo = max(0, center - period // 3)
        hi = min(n, center + period // 3)
        candidates.append(lo + int(np.argmax(env[lo:hi])))
    return np.array(sorted(set(candidates)), dtype=int)


def hit_peak_levels(
    x: np.ndarray, onsets: np.ndarray, sr: float, window_ms: float = 30.0
) -> np.ndarray:
    """Peak level (dB) inside a short window after each onset."""
    win = int(round(window_ms / 1000.0 * sr))
    levels = []
    for o in onsets:
        seg = x[o : o + win]
        levels.append(db(np.max(np.abs(seg))))
    return np.array(levels)


def relative_level_spread(levels_db: np.ndarray) -> dict[str, float]:
    """Spread statistics describing how much inter-hit dynamics survive."""
    if len(levels_db) < 2:
        return {"spread_db": 0.0, "stdev_db": 0.0, "range_db": 0.0}
    return {
        "spread_db": float(levels_db.max() - levels_db.min()),
        "stdev_db": float(np.std(levels_db)),
        "range_db": float(levels_db.max() - levels_db.min()),
    }


def recovery_curve(
    gain: np.ndarray,
    trigger_idx: int,
    sr: float,
    window_ms: float = 600.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Gain-reduction recovery after a peak, in ms and dB of reduction."""
    win = int(round(window_ms / 1000.0 * sr))
    end = min(len(gain), trigger_idx + win)
    seg = gain[trigger_idx:end]
    t_ms = np.arange(len(seg)) / sr * 1000.0
    reduction_db = -db(seg)  # positive numbers while limiting
    return t_ms, reduction_db


def time_to_recover(reduction_db: np.ndarray, t_ms: np.ndarray, floor_db: float = 0.1) -> float:
    """Time until gain reduction falls below ``floor_db`` (ms)."""
    idx = np.flatnonzero(reduction_db < floor_db)
    return float(t_ms[idx[0]]) if len(idx) else float("nan")


def deepest_reduction_index(gain: np.ndarray) -> int:
    """Sample index of the maximum gain reduction."""
    return int(np.argmin(gain))


def ceiling_error(true_peak_db: float, ceiling_db: float) -> float:
    """Signed error of the output true peak relative to the ceiling (dB)."""
    return float(true_peak_db - ceiling_db)
