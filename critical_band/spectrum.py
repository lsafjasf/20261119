"""短时频谱分析：窗函数、帧切分与归一化/窗补偿。

两套并行的归一化口径
--------------------
1. 幅度谱（amplitude-consistent）
       A[k] = 2|X[k]| / S1,  S1 = sum(w)
   正弦峰在谱线上直接读出其线性峰值，S1/win_length 即窗的相干增益。

2. 功率谱 / PSD（energy-consistent，带内能量用这套）
       P[k] = 2|X[k]|^2 / (n_fft * S2),  S2 = sum(w^2)   (内部 bin)
       PSD[k] = 2|X[k]|^2 / (fs * S2)                     (W/Hz 口径)
   由 Parseval 定理 sum_k P[k] ≈ 帧信号均方值；零填充只加密谱线，
   n_fft 同时出现在 DFT 归一化中，因此改变 n_fft 不改变带内能量。
   DC/Nyquist bin 不取单边因子 2。

等效噪声带宽（bin）：ENBW = n_fft * S2 / S1^2。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .bark import BARK_BANDS, BAND_LOWER, BAND_UPPER


def get_window(name: str, length: int) -> np.ndarray:
    """返回周期型（periodic）窗；rect 为矩形窗。"""
    if length <= 0:
        raise ValueError("窗长必须为正整数")
    name = name.lower()
    n = np.arange(length)
    if name == "rect":
        return np.ones(length, dtype=float)
    if name == "hann":
        return 0.5 - 0.5 * np.cos(2 * np.pi * n / length)
    if name == "hamming":
        return 0.54 - 0.46 * np.cos(2 * np.pi * n / length)
    if name == "blackman":
        return (0.42 - 0.5 * np.cos(2 * np.pi * n / length)
                + 0.08 * np.cos(4 * np.pi * n / length))
    raise ValueError(f"未知窗类型: {name!r}")


def window_factors(window: np.ndarray) -> dict[str, float]:
    """窗的补偿因子：相干增益、能量增益、ENBW（bin）。"""
    s1 = float(np.sum(window))
    s2 = float(np.sum(window ** 2))
    n = len(window)
    return {
        "S1_sum": s1,
        "S2_energy": s2,
        "coherent_gain": s1 / n,
        "enbw_bins": n * s2 / (s1 ** 2),
    }


def _frame_signal(x: np.ndarray, win_length: int, hop: int) -> np.ndarray:
    """两端各补 win_length//2 个零后切帧，返回 (n_frames, win_length)。"""
    pad = win_length // 2
    padded = np.pad(x, (pad, pad), mode="constant")
    n_frames = 1 + (len(padded) - win_length) // hop
    if n_frames <= 0:
        raise ValueError("信号短于一个分析窗，无法切帧")
    idx = np.arange(win_length)[None, :] + hop * np.arange(n_frames)[:, None]
    return padded[idx]


@dataclass
class SpectrumFrames:
    """逐帧频谱结果。"""

    freqs: np.ndarray            # 单边频率轴 Hz
    amp_spec: np.ndarray         # (帧, bin) 幅度谱（峰值口径）
    power_spec: np.ndarray       # (帧, bin) 均方功率谱（能量口径）
    psd: np.ndarray              # (帧, bin) 功率谱密度
    band_power: np.ndarray       # (帧, 24) 各临界带均方能量
    win_factors: dict[str, float]
    n_fft: int
    win_length: int


def stft(x: np.ndarray, params) -> SpectrumFrames:
    """按 ModelParams 计算短时频谱与逐帧临界带能量。"""
    if params.n_fft < params.win_length:
        raise ValueError("n_fft 必须 >= win_length（零填充分析）")
    win = get_window(params.window, params.win_length)
    s1, s2 = float(np.sum(win)), float(np.sum(win ** 2))
    hop = max(1, int(round(params.win_length * params.hop_ratio)))
    frames = _frame_signal(np.asarray(x, dtype=float),
                           params.win_length, hop)
    windowed = frames * win[None, :]
    # rfft 自带 1/n_fft 之外的 DFT；这里手动归一化
    spec = np.fft.rfft(windowed, n=params.n_fft, axis=1)
    mag = np.abs(spec)
    n_bins = mag.shape[1]
    freqs = np.fft.rfftfreq(params.n_fft, d=1.0 / params.fs)

    amp = np.empty_like(mag)
    power = np.empty_like(mag)
    psd = np.empty_like(mag)
    amp[:] = mag / s1
    power[:] = mag ** 2 / (params.n_fft * s2)
    psd[:] = mag ** 2 / (params.fs * s2)
    if n_bins > 1:
        amp[:, 1:-1] *= 2.0
        power[:, 1:-1] *= 2.0
        psd[:, 1:-1] *= 2.0

    band_power = np.zeros((frames.shape[0], len(BARK_BANDS)))
    for bi, band in enumerate(BARK_BANDS):
        in_band = (freqs >= band.lower) & (freqs < band.upper)
        band_power[:, bi] = np.sum(power[:, in_band], axis=1)

    return SpectrumFrames(
        freqs=freqs,
        amp_spec=amp,
        power_spec=power,
        psd=psd,
        band_power=band_power,
        win_factors=window_factors(win),
        n_fft=params.n_fft,
        win_length=params.win_length,
    )


def band_index_mask(freqs: np.ndarray) -> np.ndarray:
    """把任意频率轴上的 bin 映射到临界带编号（0 表示表外）。"""
    idx = np.searchsorted(BAND_UPPER, freqs, side="left") + 1
    out = (freqs < BAND_LOWER[0]) | (freqs > BAND_UPPER[-1])
    return np.where(out, 0, idx)
