"""Synthetic exposure sequences with a *known* camera response curve.

The scene is fully static except for one moving disk so that ghosting and
reference-frame behaviour can be evaluated deterministically.
"""

from dataclasses import dataclass

import numpy as np

Z_MAX = 255


@dataclass
class TestSequence:
    images: np.ndarray            # (N, H, W, 3) uint8 observed frames
    exposures: np.ndarray         # (N,) exposure times (seconds)
    irradiance: np.ndarray        # (H, W, 3) ground-truth linear irradiance
    static_mask: np.ndarray       # (H, W) True where nothing ever moves
    motion_gt: np.ndarray         # (H, W) True where the disk is in any frame
    crf: np.ndarray               # (256, 3) inverse response g(z) = log irradiance
    outlier_index: int


def make_crf(gamma=2.2, strength=0.6, seed=0):
    """Build a smooth non-linear inverse response curve g(z).

    g maps an 8-bit value to log-linear exposure (log(E*t)).  The curve is a
    blend between a pure gamma curve and a gentle S-shape, anchored so that
    g(128) = 0 (mid-gray corresponds to unit log exposure).
    """
    rng = np.random.default_rng(seed)
    z = np.arange(256, dtype=np.float64)
    x = z / Z_MAX
    gamma_part = np.log(np.maximum(x, 1e-4)) * gamma
    s_part = 4.0 * (x - 0.5) ** 3 + 1.5 * (x - 0.5)
    # Compress the dynamic range: a gamma-only curve spans ~11 log units over
    # 8 bits which makes quantisation noise dominate; blend in the S-curve and
    # rescale to a realistic ~6 log-unit span.
    g = (1.0 - strength) * gamma_part + strength * s_part * gamma
    g *= 0.55
    per_channel = rng.normal(0.0, 0.04, size=(3,))
    g = g[:, None] + per_channel[None, :]
    g -= g[128][None, :]
    return g


def _disk(mask, cy, cx, radius, value=True):
    h, w = mask.shape
    yy, xx = np.mgrid[0:h, 0:w]
    mask[(yy - cy) ** 2 + (xx - cx) ** 2 <= radius ** 2] = value


def _scene_irradiance(h, w, seed=1):
    """Linear irradiance scene: smooth gradient + checker + saturated lamp."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    grad = 0.25 + 0.55 * (xx / max(w - 1, 1))
    check = np.where(((xx // 16) + (yy // 16)) % 2 == 0, 1.25, 0.8)
    # Dense, smooth random texture so the bracketed sequence observes many
    # distinct intensity levels (needed to identify the full response curve).
    coarse = rng.uniform(0.55, 1.8, size=(h // 8 + 1, w // 8 + 1))
    gy, gx = np.mgrid[0:h, 0:w]
    y0, x0 = (gy // 8).astype(int), (gx // 8).astype(int)
    texture = coarse[y0, x0]
    waves = 1.0 + 0.12 * np.sin(xx / 6.0) * np.cos(yy / 5.0)
    base = grad * check * texture * waves
    irr = np.stack([base * 0.9, base, base * 1.1], axis=-1)
    lamp = np.zeros((h, w), dtype=bool)
    _disk(lamp, h * 0.28, w * 0.72, min(h, w) * 0.10)
    # Keep the lamp inside the bracketed dynamic range so every channel keeps
    # valid (non-clipped) observations linking the bright end of the curve.
    irr[lamp] = np.array([9.0, 8.0, 7.0])
    # A small specular core exceeds the brightest exposure in every frame, so
    # it is permanently clipped and must be reported as unrecoverable.
    core = np.zeros((h, w), dtype=bool)
    _disk(core, h * 0.28, w * 0.72, min(h, w) * 0.035)
    irr[core] = np.array([400.0, 360.0, 320.0])
    irr += rng.normal(0.0, 0.004, size=irr.shape)
    return np.maximum(irr, 1e-3), lamp


def _observe(g, log_exposure, shot_sigma, rng):
    """Apply forward response, clip to 8 bit, add read/shot noise."""
    log_et = np.log(np.maximum(log_exposure, 1e-6))
    z = np.empty_like(log_et)
    for c in range(3):
        z[..., c] = np.interp(log_et[..., c], g[:, c], np.arange(256))
    z = z + rng.normal(0.0, shot_sigma, size=z.shape)
    return np.clip(np.rint(z), 0, Z_MAX).astype(np.uint8)


def generate_test_sequence(h=96, w=128, n_frames=11, seed=2):
    """Generate a bracketed sequence containing one outlier and a moving disk."""
    rng = np.random.default_rng(seed)
    g = make_crf(seed=seed)
    irr, lamp = _scene_irradiance(h, w, seed=seed)

    stops = np.linspace(-3.0, 3.0, n_frames)
    exposures = 2.0 ** stops * 0.125
    outlier = n_frames // 2

    radius = min(h, w) * 0.18
    start = np.array([h * 0.62, w * 0.22])
    end = np.array([h * 0.62, w * 0.62])
    motion_gt = np.zeros((h, w), dtype=bool)
    frames = []
    for i, t in enumerate(exposures):
        frame_irr = irr.copy()
        frac = i / max(n_frames - 1, 1)
        center = start + (end - start) * frac
        disk = np.zeros((h, w), dtype=bool)
        _disk(disk, center[0], center[1], radius)
        motion_gt |= disk
        frame_irr[disk] = np.array([2.4, 0.55, 0.35])

        log_et = frame_irr * t
        if i == outlier:
            # Outlier exposure: spatially non-uniform illumination glitch
            # (local flicker / flare) that a single global exposure-time
            # correction cannot explain.
            yy_o, xx_o = np.mgrid[0:h, 0:w].astype(np.float64)
            flare = 1.0 + 1.8 * np.exp(
                -(((xx_o - w * 0.35) / (w * 0.18)) ** 2
                  + ((yy_o - h * 0.55) / (h * 0.16)) ** 2)
            )
            log_et = log_et * flare[..., None]
        sigma = 0.35 if i != outlier else 0.6
        frames.append(_observe(g, log_et, sigma, rng))

    images = np.stack(frames, axis=0)
    static_mask = ~motion_gt
    return TestSequence(
        images=images,
        exposures=exposures.astype(np.float64),
        irradiance=irr,
        static_mask=static_mask,
        motion_gt=motion_gt,
        crf=g,
        outlier_index=outlier,
    )
