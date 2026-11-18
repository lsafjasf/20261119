"""帧级声学特征：对数能量、过零率(ZCR)、频谱平坦度(SFM)。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class FrameFeatures:
    times: np.ndarray        # 每帧中心时间 (秒), shape (N,)
    log_energy: np.ndarray   # 对数能量 dB, shape (N,)
    zcr: np.ndarray          # 过零率 [0,1], shape (N,)
    flatness: np.ndarray     # 频谱平坦度 [0,1], shape (N,)
    frame_ms: float
    hop_ms: float


def frame_signal(x: np.ndarray, sr: int, frame_ms: float = 25.0,
                 hop_ms: float = 10.0) -> tuple[np.ndarray, np.ndarray]:
    """分帧，返回 (frames, 帧中心时间)。frames shape: (N, frame_len)。"""
    frame_len = max(1, int(round(sr * frame_ms / 1000.0)))
    hop = max(1, int(round(sr * hop_ms / 1000.0)))
    if len(x) < frame_len:
        x = np.pad(x, (0, frame_len - len(x)))
    n_frames = 1 + (len(x) - frame_len) // hop
    idx = np.arange(frame_len)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = x[idx]
    centers = (idx[:, 0] + frame_len / 2.0) / sr
    return frames, centers


def extract(x: np.ndarray, sr: int, frame_ms: float = 25.0,
            hop_ms: float = 10.0) -> FrameFeatures:
    """从单声道浮点波形 ([-1,1]) 提取帧级特征。"""
    x = np.asarray(x, dtype=np.float64)
    frames, centers = frame_signal(x, sr, frame_ms, hop_ms)
    window = np.hanning(frames.shape[1])
    win = frames * window

    # 对数能量 (dB)，加小量防止 log(0)
    energy = np.sum(win ** 2, axis=1) / frames.shape[1]
    log_energy = 10.0 * np.log10(energy + 1e-12)

    # 过零率（未加窗的帧）
    signs = np.signbit(frames)
    zcr = np.mean(signs[:, 1:] != signs[:, :-1], axis=1)

    # 频谱平坦度 = 几何均值 / 算术均值（功率谱）
    spec = np.abs(np.fft.rfft(win, axis=1)) ** 2
    spec = np.maximum(spec, 1e-16)
    geo = np.exp(np.mean(np.log(spec), axis=1))
    arith = np.mean(spec, axis=1)
    flatness = geo / arith

    return FrameFeatures(
        times=centers,
        log_energy=log_energy,
        zcr=zcr,
        flatness=flatness,
        frame_ms=frame_ms,
        hop_ms=hop_ms,
    )
