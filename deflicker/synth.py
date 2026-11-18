"""合成测试素材。

场景 = 恒定纹理背景 + 移动圆形前景。
观测视频 = 场景内容 * 已知周期调制 g_flicker(t) * 真实开灯阶跃 g_step(t)。
理想输出 = 场景内容 * g_step(t)（保留真实照明变化，去除频闪）。
"""
from __future__ import annotations

import json
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
from PIL import Image


def make_background(h: int, w: int, seed: int = 7) -> np.ndarray:
    """低频纹理 + 水平渐变的恒定背景，取值 [0, 1]，形状 (h, w, 3)。"""
    rng = np.random.default_rng(seed)
    small = rng.random((h // 16 + 2, w // 16 + 2, 3))
    img = Image.fromarray((small * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    tex = np.asarray(img, np.float64) / 255.0
    yy, xx = np.mgrid[0:h, 0:w]
    grad = 0.28 + 0.13 * (xx / w) + 0.05 * np.sin(yy / h * np.pi)
    return np.clip(tex * 0.20 + grad[..., None], 0.0, 1.0)


def foreground_alpha(h: int, w: int, t: int, n: int) -> tuple[np.ndarray, np.ndarray]:
    """第 t 帧的移动圆盘前景，返回 (alpha (h,w,1), color (3,))。"""
    cx = w * (0.5 + 0.32 * np.sin(2 * np.pi * t / n * 2))
    cy = h * (0.5 + 0.28 * np.cos(2 * np.pi * t / n * 3))
    radius = min(h, w) * 0.13
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.hypot(xx - cx, yy - cy)
    alpha = np.clip(radius - dist, 0.0, 1.0)[..., None]
    color = np.array([0.60, 0.20, 0.15])
    return alpha, color


def synthesize(
    out_dir: str | Path,
    n: int = 120,
    h: int = 240,
    w: int = 320,
    fps: int = 30,
    flicker_hz: float = 4.0,
    flicker_amp: float = 0.18,
    step_frame: int = 70,
    step_gain: float = 1.25,
    seed: int = 7,
) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    bg = make_background(h, w, seed)
    t = np.arange(n)
    g_flicker = 1.0 + flicker_amp * np.sin(2 * np.pi * flicker_hz * t / fps)
    g_step = np.where(t >= step_frame, step_gain, 1.0)

    observed = np.empty((n, h, w, 3), np.float32)
    ideal = np.empty((n, h, w, 3), np.float32)
    alpha_all = np.empty((n, h, w), np.uint8)
    for i in range(n):
        alpha, color = foreground_alpha(h, w, i, n)
        content = bg * (1 - alpha) + color[None, None, :] * alpha
        observed[i] = np.clip(content * g_flicker[i] * g_step[i], 0, 1)
        ideal[i] = np.clip(content * g_step[i], 0, 1)
        alpha_all[i] = (alpha[..., 0] * 255).astype(np.uint8)

    for name, frames in (("flicker.mp4", observed), ("ideal.mp4", ideal)):
        writer = imageio.get_writer(
            str(out / name), fps=fps, codec="libx264", quality=9, macro_block_size=None
        )
        for frame in frames:
            writer.append_data((frame * 255).round().astype(np.uint8))
        writer.close()

    np.savez_compressed(
        out / "gt.npz",
        g_flicker=g_flicker.astype(np.float32),
        g_step=g_step.astype(np.float32),
        fg_alpha=alpha_all,
    )
    meta = dict(
        n=n, h=h, w=w, fps=fps, flicker_hz=flicker_hz, flicker_amp=flicker_amp,
        step_frame=step_frame, step_gain=step_gain, seed=seed,
    )
    (out / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return meta
