"""Motion-trajectory kernels and image formation helpers."""

from __future__ import annotations

import numpy as np
from scipy import ndimage


def _add_gaussian_dot(kernel: np.ndarray, y: float, x: float, sigma: float) -> None:
    radius = max(2, int(np.ceil(3.0 * sigma)))
    y0 = int(np.floor(y)) - radius
    y1 = int(np.ceil(y)) + radius + 1
    x0 = int(np.floor(x)) - radius
    x1 = int(np.ceil(x)) + radius + 1
    yy, xx = np.mgrid[y0:y1, x0:x1]
    stamp = np.exp(-0.5 * ((yy - y) ** 2 + (xx - x) ** 2) / sigma**2)
    iy0, iy1 = max(0, y0), min(kernel.shape[0], y1)
    ix0, ix1 = max(0, x0), min(kernel.shape[1], x1)
    if iy0 >= iy1 or ix0 >= ix1:
        return
    kernel[iy0:iy1, ix0:ix1] += stamp[
        iy0 - y0 : iy1 - y0, ix0 - x0 : ix1 - x0
    ]


def trajectory_kernel(
    points: list[tuple[float, float]],
    size: int,
    *,
    sigma: float = 0.7,
    samples_per_pixel: int = 8,
) -> np.ndarray:
    """Rasterize a short camera trajectory into a normalized kernel.

    Coordinates are ``(x, y)`` offsets from the kernel center. The trajectory
    is sampled densely enough that sub-pixel lines remain continuous.
    """
    if size < 3 or size % 2 == 0:
        raise ValueError("kernel size must be an odd integer >= 3")
    if len(points) < 2:
        raise ValueError("a trajectory needs at least two points")

    kernel = np.zeros((size, size), dtype=np.float64)
    center = (size - 1) / 2.0
    for (x0, y0), (x1, y1) in zip(points[:-1], points[1:]):
        length = float(np.hypot(x1 - x0, y1 - y0))
        steps = max(2, int(np.ceil(length * samples_per_pixel)) + 1)
        for t in np.linspace(0.0, 1.0, steps):
            _add_gaussian_dot(
                kernel,
                center + (y0 + t * (y1 - y0)),
                center + (x0 + t * (x1 - x0)),
                sigma,
            )
    total = kernel.sum()
    if total <= 0:
        raise ValueError("empty trajectory kernel")
    return kernel / total


def gaussian_kernel(size: int, sigma: float) -> np.ndarray:
    """Return a normalized, centered isotropic Gaussian kernel."""
    if size < 3 or size % 2 == 0:
        raise ValueError("kernel size must be an odd integer >= 3")
    if sigma <= 0:
        raise ValueError("sigma must be positive")
    c = (size - 1) / 2.0
    yy, xx = np.mgrid[:size, :size]
    kernel = np.exp(-0.5 * ((yy - c) ** 2 + (xx - c) ** 2) / sigma**2)
    return kernel / kernel.sum()


def embed_kernel(kernel: np.ndarray, canvas_size: int) -> np.ndarray:
    """Center an odd-sized kernel in a larger square canvas."""
    if kernel.shape[0] > canvas_size or kernel.shape[1] > canvas_size:
        raise ValueError("kernel is larger than the embedding canvas")
    out = np.zeros((canvas_size, canvas_size), dtype=np.float64)
    y0 = (canvas_size - kernel.shape[0]) // 2
    x0 = (canvas_size - kernel.shape[1]) // 2
    out[y0 : y0 + kernel.shape[0], x0 : x0 + kernel.shape[1]] = kernel
    return out


def blur_image(image: np.ndarray, kernel: np.ndarray, *, circular: bool = True) -> np.ndarray:
    """Convolve a 2-D image with a compact kernel."""
    if image.ndim != 2 or kernel.ndim != 2:
        raise ValueError("image and kernel must be 2-D")
    mode = "wrap" if circular else "reflect"
    return ndimage.convolve(image, kernel, mode=mode)


def add_noise(image: np.ndarray, sigma: float, seed: int) -> np.ndarray:
    """Add reproducible Gaussian observation noise."""
    rng = np.random.default_rng(seed)
    return np.clip(image + rng.normal(0.0, sigma, image.shape), 0.0, 1.0)


def centered_gaussian_on_canvas(canvas_size: int, sigma: float) -> np.ndarray:
    """Truth-blind initialization independent of any generated trajectory."""
    width = max(3, min(canvas_size, int(2 * np.ceil(3 * sigma) + 1)))
    if width % 2 == 0:
        width -= 1
    return embed_kernel(gaussian_kernel(width, sigma), canvas_size)


def kernel_moments(kernel: np.ndarray) -> tuple[float, float]:
    """Return the mass centroid of a non-negative kernel."""
    if kernel.size == 0 or kernel.sum() <= 0:
        raise ValueError("kernel must have positive mass")
    yy, xx = np.mgrid[: kernel.shape[0], : kernel.shape[1]]
    mass = kernel.sum()
    return float((yy * kernel).sum() / mass), float((xx * kernel).sum() / mass)


def smooth_image(image: np.ndarray, sigma: float = 1.0) -> np.ndarray:
    return ndimage.gaussian_filter(image, sigma=sigma, mode="reflect")
