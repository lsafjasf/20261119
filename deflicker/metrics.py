"""评估指标：残余周期能量、前景亮度误差。"""
from __future__ import annotations

import numpy as np


def dominant_frequency(s: np.ndarray, fps: float, min_hz: float = 0.5) -> float:
    """去直流后增益曲线的主频（Hz）。"""
    x = s - s.mean()
    spec = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(len(x), d=1.0 / fps)
    valid = freqs >= min_hz
    if not valid.any():
        return 0.0
    return float(freqs[valid][np.argmax(spec[valid])])


def periodic_energy(s: np.ndarray, fps: float, f0: float, band: float = 0.6) -> float:
    """增益曲线在 f0 ± band (Hz) 频带内的能量。"""
    x = s - s.mean()
    spec = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(len(x), d=1.0 / fps)
    sel = (freqs >= f0 - band) & (freqs <= f0 + band)
    return float(spec[sel].sum())


def residual_periodic_ratio(
    flicker_before: np.ndarray,
    flicker_after: np.ndarray,
    fps: float,
    f0: float | None = None,
) -> dict:
    """补偿前后周期分量的带内能量对比。"""
    if f0 is None:
        f0 = dominant_frequency(flicker_before, fps)
    e_before = periodic_energy(flicker_before, fps, f0)
    e_after = periodic_energy(flicker_after, fps, f0)
    return {
        "f0_hz": f0,
        "energy_before": e_before,
        "energy_after": e_after,
        "residual_ratio": e_after / e_before if e_before > 0 else 0.0,
    }


def foreground_brightness_error(
    corrected: np.ndarray, ideal: np.ndarray, fg_alpha: np.ndarray
) -> tuple[np.ndarray, dict]:
    """前景区域亮度误差：corrected/ideal 为 (T,H,W,3) [0,1]，fg_alpha 为 (T,H,W) 0-255。"""
    t = corrected.shape[0]
    errs = np.zeros(t)
    for i in range(t):
        w = fg_alpha[i].astype(np.float32) / 255.0
        if w.sum() < 1:
            continue
        diff = np.abs(corrected[i] - ideal[i]).mean(axis=2)
        errs[i] = float((diff * w).sum() / w.sum())
    stats = {
        "fg_err_mean": float(errs.mean()),
        "fg_err_max": float(errs.max()),
        "fg_err_p95": float(np.percentile(errs, 95)),
    }
    return errs, stats
