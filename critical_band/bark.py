"""Bark 临界带频率映射。

核心数据是显式列出的 24 个 Zwicker 临界带（Zwicker & Fastl,
《Psychoacoustics》常用表）。每个频带给出下限/中心/上限频率（Hz），
相邻频带首尾相接，覆盖 20 Hz–15.5 kHz。

除显式表外，提供标准解析公式（Zwicker, 1961）作为双向换算的补充：

    z(f) = 13 * atan(0.00076 f) + 3.5 * atan((f / 7500)^2)

频带划分一律以显式表为准，解析公式只用于连续频率的 Bark 坐标换算。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BarkBand:
    """单个临界带的描述（频率单位均为 Hz）。"""

    index: int  # 1..24
    lower: float
    center: float
    upper: float

    @property
    def width_hz(self) -> float:
        return self.upper - self.lower


# 显式 24 带表：(下限, 中心, 上限) Hz
_BAND_TABLE = (
    (20.0, 50.0, 100.0),
    (100.0, 150.0, 200.0),
    (200.0, 250.0, 300.0),
    (300.0, 350.0, 400.0),
    (400.0, 450.0, 510.0),
    (510.0, 570.0, 630.0),
    (630.0, 700.0, 770.0),
    (770.0, 840.0, 920.0),
    (920.0, 1000.0, 1080.0),
    (1080.0, 1170.0, 1270.0),
    (1270.0, 1370.0, 1480.0),
    (1480.0, 1600.0, 1720.0),
    (1720.0, 1850.0, 2000.0),
    (2000.0, 2150.0, 2320.0),
    (2320.0, 2500.0, 2700.0),
    (2700.0, 2900.0, 3150.0),
    (3150.0, 3400.0, 3700.0),
    (3700.0, 4000.0, 4400.0),
    (4400.0, 4800.0, 5300.0),
    (5300.0, 5800.0, 6400.0),
    (6400.0, 7000.0, 7700.0),
    (7700.0, 8500.0, 9500.0),
    (9500.0, 10500.0, 12000.0),
    (12000.0, 13500.0, 15500.0),
)

BARK_BANDS: tuple[BarkBand, ...] = tuple(
    BarkBand(index=i + 1, lower=lo, center=fc, upper=hi)
    for i, (lo, fc, hi) in enumerate(_BAND_TABLE)
)

# 便于向量化查表的数组
BAND_LOWER = np.array([b.lower for b in BARK_BANDS])
BAND_CENTER = np.array([b.center for b in BARK_BANDS])
BAND_UPPER = np.array([b.upper for b in BARK_BANDS])


def bark_from_freq(freq_hz: float | np.ndarray) -> float | np.ndarray:
    """用 Zwicker 解析公式把频率（Hz）换算为连续 Bark 坐标。"""
    f = np.asarray(freq_hz, dtype=float)
    z = 13.0 * np.arctan(0.00076 * f) + 3.5 * np.arctan((f / 7500.0) ** 2)
    return float(z) if np.ndim(freq_hz) == 0 else z


def freq_from_bark(bark: float | np.ndarray) -> float | np.ndarray:
    """Bark 坐标反算频率（Hz），对解析公式做数值反解（二分法）。"""
    scalar = np.ndim(bark) == 0
    values = np.atleast_1d(np.asarray(bark, dtype=float))
    result = np.empty_like(values)
    for i, z in enumerate(values):
        lo, hi = 0.0, 16000.0
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if bark_from_freq(mid) < z:
                lo = mid
            else:
                hi = mid
        result[i] = 0.5 * (lo + hi)
    return float(result[0]) if scalar else result


def assign_band(freq_hz: float | np.ndarray) -> int | np.ndarray:
    """按显式 24 带表把频率分配到频带编号（1..24）。

    频带为左闭右开区间 [lower, upper)，带界频率归入更高的带。
    低于 20 Hz 或高于 15.5 kHz 的频率返回 0，表示落在表外。
    """
    f = np.asarray(freq_hz, dtype=float)
    # 左闭右开 [lower, upper)：f == upper[i] 应归入第 i+2 带
    idx = np.searchsorted(BAND_UPPER, f, side="right") + 1
    out_of_range = (f < BAND_LOWER[0]) | (f > BAND_UPPER[-1])
    idx = np.where(out_of_range, 0, idx)
    return int(idx) if np.ndim(freq_hz) == 0 else idx
