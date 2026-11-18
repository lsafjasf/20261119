"""测试信号生成：单音、近邻双音、宽带噪声与可移动探测音。

所有幅度以 dBFS（相对数字满量程）给出，0 dBFS 对应幅度 1.0 的正弦。
信号默认做峰值归一化（normalize=True），使混合信号峰值不超过 -1 dBFS，
避免后续分析削波；各分量之间的相对幅度保持不变。
"""

from __future__ import annotations

import numpy as np


def dbfs_to_amp(dbfs: float) -> float:
    """dBFS 转线性幅度（正弦峰值）。"""
    return float(10.0 ** (dbfs / 20.0))


def _time_axis(n: int, fs: float) -> np.ndarray:
    return np.arange(n, dtype=float) / fs


def _normalize_peak(x: np.ndarray, headroom_db: float = 1.0) -> np.ndarray:
    """峰值归一化到 -headroom_db dBFS；静音原样返回。"""
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak <= 1e-12:
        return x
    target = dbfs_to_amp(-headroom_db)
    return x * (target / peak)


def make_tone(freq_hz: float, amp_dbfs: float, fs: float, duration_s: float,
              phase: float = 0.0) -> np.ndarray:
    """单频正弦。"""
    n = int(round(fs * duration_s))
    t = _time_axis(n, fs)
    return dbfs_to_amp(amp_dbfs) * np.sin(2 * np.pi * freq_hz * t + phase)


def make_two_tone(f1: float, f2: float, amp1_dbfs: float, amp2_dbfs: float,
                  fs: float, duration_s: float) -> np.ndarray:
    """近邻双音（两个正弦之和）。"""
    return (make_tone(f1, amp1_dbfs, fs, duration_s)
            + make_tone(f2, amp2_dbfs, fs, duration_s))


def make_broadband_noise(amp_dbfs: float, fs: float, duration_s: float,
                         seed: int = 42) -> np.ndarray:
    """宽带白噪声（高斯），按给定 RMS 幅度（dBFS）生成。

    对噪声而言 dBFS 指 RMS 水平；生成后按样本 RMS 精确标定。
    """
    rng = np.random.default_rng(seed)
    n = int(round(fs * duration_s))
    x = rng.standard_normal(n)
    rms = float(np.sqrt(np.mean(x ** 2)))
    target_rms = dbfs_to_amp(amp_dbfs)
    return x * (target_rms / rms)


def make_signal(kind: str, fs: float, duration_s: float,
                normalize: bool = True, **kw) -> np.ndarray:
    """按名称生成测试信号，并叠加可选探测音。

    kind: "tone" | "two_tone" | "noise"
    公共可选参数
    ----------
    probe_freq, probe_amp_dbfs : 探测音频率(Hz)与幅度(dBFS)
    各 kind 专用参数
    ----------------
    tone:       tone_freq, tone_amp_dbfs
    two_tone:   f1, f2, amp1_dbfs, amp2_dbfs
    noise:      noise_amp_dbfs, seed
    """
    kind = kind.lower()
    if kind == "tone":
        x = make_tone(kw.get("tone_freq", 1000.0),
                      kw.get("tone_amp_dbfs", -20.0), fs, duration_s)
    elif kind == "two_tone":
        x = make_two_tone(kw.get("f1", 980.0), kw.get("f2", 1020.0),
                          kw.get("amp1_dbfs", -20.0),
                          kw.get("amp2_dbfs", -26.0), fs, duration_s)
    elif kind == "noise":
        x = make_broadband_noise(kw.get("noise_amp_dbfs", -20.0), fs,
                                 duration_s, seed=kw.get("seed", 42))
    else:
        raise ValueError(f"未知信号类型: {kind!r}")

    probe_freq = kw.get("probe_freq")
    if probe_freq is not None:
        probe = make_tone(probe_freq,
                          kw.get("probe_amp_dbfs", -30.0), fs, duration_s)
        x = x + probe

    if normalize:
        x = _normalize_peak(x)
    return x.astype(float)
