"""Synthetic scenes with known trajectories used only for evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from .trajectory import add_noise, blur_image, trajectory_kernel


@dataclass(frozen=True)
class SampleSpec:
    key: str
    label: str
    category: str
    image: np.ndarray
    trajectory: tuple[tuple[float, float], ...]
    truth_kernel_size: int
    noise_sigma: float
    seed: int
    notes: str = ""
    metadata: dict[str, float | str] = field(default_factory=dict)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    )
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def text_edge_scene(size: int = 192) -> np.ndarray:
    """Crisp typography, bars, and diagonal edges."""
    canvas = Image.new("L", (size, size), color=238)
    draw = ImageDraw.Draw(canvas)
    draw.text((16, 13), "BLIND", font=_font(48), fill=18)
    draw.text((20, 66), "MOTION", font=_font(41), fill=30)
    draw.rectangle((17, 119, 163, 126), fill=20)
    draw.rectangle((17, 139, 118, 146), fill=70)
    draw.polygon([(20, 176), (96, 158), (174, 180), (174, 186), (20, 184)], fill=12)
    draw.line([(116, 88), (178, 35)], fill=15, width=4)
    draw.line([(128, 96), (184, 112)], fill=55, width=2)
    return np.asarray(canvas, dtype=np.float64) / 255.0


def texture_block_scene(size: int = 192) -> np.ndarray:
    """Mixed high-frequency bars, checker blocks, and granular texture."""
    rng = np.random.default_rng(17)
    y, x = np.mgrid[:size, :size]
    image = 0.72 + 0.07 * np.sin(0.35 * x) * np.cos(0.19 * y)

    checker = ((x // 12 + y // 12) % 2).astype(np.float64)
    region = (slice(10, 70), slice(8, 98))
    image[region] = 0.20 + 0.62 * checker[region]

    bars = 0.5 + 0.42 * np.sign(np.sin(0.72 * x + 0.10 * y))
    image[78:134, 8:88] = bars[78:134, 8:88]

    image[10:62, 108:184] = np.clip(
        ndimage.gaussian_filter(rng.normal(0.5, 0.32, (52, 76)), 0.55),
        0.0,
        1.0,
    )
    image[143:184, 8:184] = np.clip(
        0.5 + 0.30 * np.sin(0.5 * x[143:184, 8:184])
        + 0.18 * np.cos(0.9 * y[143:184, 8:184]),
        0.0,
        1.0,
    )
    image[78:135, 108:184] = 0.42
    circle = (x[78:135, 108:184] - 36) ** 2 + (y[78:135, 108:184] - 28) ** 2 < 20**2
    image[78:135, 108:184][circle] = 0.92
    return np.clip(image, 0.0, 1.0)


def noise_scene(size: int = 192, seed: int = 31) -> np.ndarray:
    """Fine random structure: blur alone cannot identify a unique trajectory."""
    rng = np.random.default_rng(seed)
    high_frequency = rng.normal(0.5, 0.28, (size, size))
    fine = ndimage.gaussian_filter(rng.normal(0.0, 0.22, (size, size)), 0.45)
    image = np.clip(0.5 + high_frequency - 0.5 + fine, 0.0, 1.0)
    return image


def low_texture_scene(size: int = 192) -> np.ndarray:
    """A deliberately ambiguous, nearly flat input."""
    y, x = np.mgrid[:size, :size]
    image = 0.51 + 0.008 * np.sin(0.035 * x) + 0.007 * np.cos(0.028 * y)
    image[91:101, 61:131] += 0.010
    image[61:131, 91:101] += 0.006
    return np.clip(image, 0.0, 1.0)


STRAIGHT = ((-5.5, 0.0), (5.5, 0.0))
POLYLINE = ((-6.0, -2.5), (-2.0, 2.5), (2.5, 1.0), (6.0, -2.0))


def build_sample_specs(size: int = 192) -> list[SampleSpec]:
    """Create all scenes; trajectories are retained solely for scoring/display."""
    text = text_edge_scene(size)
    texture = texture_block_scene(size)
    noise = noise_scene(size)
    low_texture = low_texture_scene(size)
    return [
        SampleSpec(
            key="text_straight",
            label="Text edges / straight",
            category="text_edges",
            image=text,
            trajectory=STRAIGHT,
            truth_kernel_size=13,
            noise_sigma=0.006,
            seed=101,
            notes="Crisp glyph and bar edges make horizontal motion observable.",
        ),
        SampleSpec(
            key="text_polyline",
            label="Text edges / polyline",
            category="text_edges",
            image=text,
            trajectory=POLYLINE,
            truth_kernel_size=15,
            noise_sigma=0.006,
            seed=102,
            notes="The kink in the trajectory adds a second motion direction.",
        ),
        SampleSpec(
            key="texture_straight",
            label="Texture block / straight",
            category="texture_blocks",
            image=texture,
            trajectory=STRAIGHT,
            truth_kernel_size=13,
            noise_sigma=0.008,
            seed=201,
            notes="Multi-scale texture supplies motion information in both axes.",
        ),
        SampleSpec(
            key="texture_polyline",
            label="Texture block / polyline",
            category="texture_blocks",
            image=texture,
            trajectory=POLYLINE,
            truth_kernel_size=15,
            noise_sigma=0.008,
            seed=202,
            notes="Dense structure helps resolve a short kinked path.",
        ),
        SampleSpec(
            key="noise_straight",
            label="Noise sample / straight",
            category="noise",
            image=noise,
            trajectory=STRAIGHT,
            truth_kernel_size=13,
            noise_sigma=0.025,
            seed=301,
            notes="Stationary random content gives weak and non-unique motion cues.",
        ),
        SampleSpec(
            key="low_texture_polyline",
            label="Low texture / polyline",
            category="low_texture",
            image=low_texture,
            trajectory=POLYLINE,
            truth_kernel_size=15,
            noise_sigma=0.003,
            seed=401,
            notes="Nearly flat input: the kernel is under-constrained; high uncertainty is expected.",
        ),
    ]


def make_observations(
    spec: SampleSpec, max_kernel_size: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return truth kernel, clean blur, and noisy solver-visible observation."""
    if spec.truth_kernel_size > max_kernel_size:
        raise ValueError("max_kernel_size is smaller than the true trajectory")
    compact = trajectory_kernel(
        list(spec.trajectory), spec.truth_kernel_size, sigma=0.72
    )
    from .trajectory import embed_kernel

    truth_canvas = embed_kernel(compact, max_kernel_size)
    clean = blur_image(spec.image, compact, circular=True)
    observed = add_noise(clean, spec.noise_sigma, spec.seed)
    return truth_canvas, clean, observed
