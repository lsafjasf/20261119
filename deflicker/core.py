"""核心算法：稳定区域增益估计、阶跃保持的趋势分离、平滑补偿。

模型：log I(x, t) = log content(x, t) + s_flicker(t) + s_trend(t)
  - s_flicker：照明频闪引起的零均值周期分量（需要去除）
  - s_trend ：真实照明变化（如开灯阶跃，需要保留）
"""
from __future__ import annotations

from dataclasses import dataclass, field

import imageio.v2 as imageio
import numpy as np

EPS = 1e-3


def read_video_gray(path: str) -> tuple[np.ndarray, float]:
    """读取视频为灰度 log 域数组 (T, H, W) float32，并返回 fps。"""
    reader = imageio.get_reader(path)
    fps = float(reader.get_meta_data().get("fps", 30))
    frames = []
    for frame in reader:
        f = frame.astype(np.float32) / 255.0
        frames.append(f.mean(axis=2) if f.ndim == 3 else f)
    reader.close()
    gray = np.stack(frames)
    return np.log(gray + EPS), fps


def read_video_color(path: str) -> tuple[np.ndarray, float]:
    reader = imageio.get_reader(path)
    fps = float(reader.get_meta_data().get("fps", 30))
    frames = [f.astype(np.float32) / 255.0 for f in reader]
    reader.close()
    return np.stack(frames), fps


def write_video(path: str, frames: np.ndarray, fps: float) -> None:
    writer = imageio.get_writer(
        path, fps=fps, codec="libx264", quality=9, macro_block_size=None
    )
    for frame in frames:
        writer.append_data((np.clip(frame, 0, 1) * 255).round().astype(np.uint8))
    writer.close()


def rects_to_mask(h: int, w: int, rects: list[tuple[int, int, int, int]]) -> np.ndarray:
    """把 [(x, y, w, h), ...] 矩形列表转成布尔掩膜（True = 被排除）。"""
    mask = np.zeros((h, w), bool)
    for x, y, rw, rh in rects:
        mask[max(y, 0) : y + rh, max(x, 0) : x + rw] = True
    return mask


def _majority3x3(mask: np.ndarray) -> np.ndarray:
    """3x3 多数滤波，去除掩膜噪点（纯 numpy 实现）。"""
    padded = np.pad(mask.astype(np.int8), 1)
    acc = np.zeros_like(mask, np.int16)
    for dy in range(3):
        for dx in range(3):
            acc += padded[dy : dy + mask.shape[0], dx : dx + mask.shape[1]]
    return acc >= 5


def estimate_stable_mask(
    log_frames: np.ndarray, exclude: np.ndarray | None = None
) -> np.ndarray:
    """估计稳定背景像素掩膜。

    前景像素在时间上变化剧烈；背景像素的时序变化完全由全局增益引起，
    减去每帧空间中位数后残差接近零。前景只短暂覆盖某像素，故用
    高分位数（而非中位数）刻画其时序波动。
    """
    t = log_frames.shape[0]
    per_frame_med = np.median(log_frames.reshape(t, -1), axis=1)
    resid = log_frames - per_frame_med[:, None, None]
    fluct = np.quantile(np.abs(resid), 0.9, axis=0)
    center = np.median(fluct)
    scale = 1.4826 * np.median(np.abs(fluct - center)) + 1e-6
    stable = fluct < max(center + 3 * scale, 0.02)
    stable = _majority3x3(stable)
    if exclude is not None:
        stable &= ~exclude
    if stable.sum() < 0.02 * stable.size:
        stable = np.ones_like(stable) if exclude is None else ~exclude
    return stable


def estimate_raw_gain(log_frames: np.ndarray, stable: np.ndarray) -> np.ndarray:
    """在稳定像素上估计每帧对数增益 s(t)（相对各自时序中位数）。"""
    pix = log_frames[:, stable]  # (T, K)
    ref = np.median(pix, axis=0)
    return np.median(pix - ref[None, :], axis=1)


def median_filter(x: np.ndarray, k: int, mode: str = "edge") -> np.ndarray:
    k = max(3, k | 1)
    pad = k // 2
    if mode == "wrap" and len(x) > k:
        padded = np.pad(x, pad, mode="wrap")
    else:
        padded = np.pad(x, pad, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, k)
    return np.median(windows, axis=1)


def moving_average(x: np.ndarray, k: int) -> np.ndarray:
    k = max(1, k)
    pad = k // 2
    padded = np.pad(x, pad, mode="edge")
    kernel = np.ones(k) / k
    return np.convolve(padded, kernel, mode="valid")[: len(x)]


def periodic_median_filter(seg: np.ndarray, k: int, period: float) -> np.ndarray:
    """段内中值滤波；边界按频闪周期对齐延拓，避免端点伪影。"""
    k = max(3, k | 1)
    pad = k // 2
    n = len(seg)
    step = max(1, int(round(period)))
    m = step * (pad // step + 1)
    if n > m + pad:
        left = seg[m - pad : m]
        right = seg[n - m : n - m + pad]
        padded = np.concatenate([left, seg, right])
    else:
        padded = np.pad(seg, pad, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, k)
    return np.median(windows, axis=1)


def detect_steps(
    s: np.ndarray, win: int = 12, thr: float = 0.05, max_steps: int = 4,
    prefilter: int = 0,
) -> list[int]:
    """在增益曲线上检测真实照明阶跃的位置（前后窗中位数差）。

    prefilter > 0 时先按该窗长做中值滤波，抑制周期分量造成的误检。
    """
    if prefilter >= 3:
        s = median_filter(s, prefilter)
    n = len(s)
    resp = np.zeros(n)
    for i in range(win, n - win):
        resp[i] = np.median(s[i : i + win]) - np.median(s[i - win : i])
    steps: list[int] = []
    pool = np.abs(resp).copy()
    for _ in range(max_steps):
        i = int(np.argmax(pool))
        if pool[i] < thr:
            break
        steps.append(i)
        pool[max(0, i - win) : i + win + 1] = 0
    return sorted(steps)


def refine_step(s: np.ndarray, step: int, search: int = 4) -> int:
    """在检测位置附近按原始曲线的最大梯度精化阶跃边界帧。"""
    lo = max(0, step - search)
    hi = min(len(s) - 1, step + search)
    if hi <= lo:
        return step
    diffs = np.diff(s[lo : hi + 1])
    sign = 1.0 if s[min(hi, len(s) - 1)] >= s[lo] else -1.0
    return lo + int(np.argmax(sign * diffs)) + 1


@dataclass
class Decomposition:
    s_raw: np.ndarray          # 原始对数增益
    trend: np.ndarray          # 趋势分量（含真实阶跃，保留）
    flicker: np.ndarray        # 周期分量（待补偿）
    steps: list[int] = field(default_factory=list)


def decompose_gain(
    s: np.ndarray, fps: float, flicker_hz: float, smooth: int = 3
) -> Decomposition:
    """把对数增益分解为趋势（含阶跃）与零均值周期分量。

    阶跃位置先检测出来，趋势在各段内独立做长窗中值滤波，
    避免阶跃被中值窗抹平后泄漏进周期分量。
    """
    period = fps / flicker_hz
    steps = [refine_step(s, i) for i in detect_steps(s, prefilter=int(round(period * 2)) | 1)]
    win = max(11, int(round(period * 2)) | 1)
    trend = np.empty_like(s)
    bounds = [0, *steps, len(s)]
    for a, b in zip(bounds[:-1], bounds[1:]):
        seg = s[a:b]
        k = min(win, len(seg) | 1 if len(seg) % 2 else len(seg) - 1)
        trend[a:b] = periodic_median_filter(seg, max(3, k), period)
    flicker = s - trend
    flicker = median_filter(flicker, smooth)
    return Decomposition(s_raw=s, trend=trend, flicker=flicker, steps=steps)


def compensate(frames: np.ndarray, flicker_log: np.ndarray) -> np.ndarray:
    """对彩色帧 (T,H,W,3) 除以周期增益分量，趋势（真实照明）保持不变。"""
    gain = np.exp(-flicker_log)[:, None, None, None]
    return np.clip(frames * gain, 0.0, 1.0)
