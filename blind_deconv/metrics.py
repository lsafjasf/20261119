"""Evaluation metrics computed only after the truth-blind estimate is frozen."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .trajectory import kernel_moments


@dataclass
class ReconstructionMetrics:
    psnr_db: float
    relative_error_pct: float
    rmse: float
    ringing_index_pct: float
    edge_overshoot_pct: float
    ringing_map: np.ndarray
    valid_slice: tuple[slice, slice]


@dataclass
class KernelMetrics:
    centroid_error_px: float
    peak_error_px: float
    ncc: float
    truth_centroid_yx: tuple[float, float]
    estimate_centroid_yx: tuple[float, float]
    truth_peak_yx: tuple[int, int]
    estimate_peak_yx: tuple[int, int]


def _valid_slice(shape: tuple[int, int], margin: int) -> tuple[slice, slice]:
    height, width = shape
    margin = min(margin, height // 4, width // 4)
    return slice(margin, height - margin), slice(margin, width - margin)


def compute_kernel_metrics(truth: np.ndarray, estimate: np.ndarray) -> KernelMetrics:
    truth_centroid = kernel_moments(truth)
    estimate_centroid = kernel_moments(estimate)
    truth_peak = tuple(int(v) for v in np.unravel_index(truth.argmax(), truth.shape))
    estimate_peak = tuple(
        int(v) for v in np.unravel_index(estimate.argmax(), estimate.shape)
    )
    a = truth - truth.mean()
    b = estimate - estimate.mean()
    denominator = float(np.sqrt(np.sum(a * a) * np.sum(b * b)))
    ncc = float(np.sum(a * b) / denominator) if denominator > 0 else 0.0
    return KernelMetrics(
        centroid_error_px=float(
            np.hypot(
                estimate_centroid[0] - truth_centroid[0],
                estimate_centroid[1] - truth_centroid[1],
            )
        ),
        peak_error_px=float(
            np.hypot(
                estimate_peak[0] - truth_peak[0],
                estimate_peak[1] - truth_peak[1],
            )
        ),
        ncc=ncc,
        truth_centroid_yx=truth_centroid,
        estimate_centroid_yx=estimate_centroid,
        truth_peak_yx=truth_peak,
        estimate_peak_yx=estimate_peak,
    )


def compute_reconstruction_metrics(
    estimate: np.ndarray,
    truth: np.ndarray,
    *,
    margin: int,
) -> ReconstructionMetrics:
    valid = _valid_slice(truth.shape, margin)
    estimate_crop = estimate[valid]
    truth_crop = truth[valid]
    difference = estimate_crop - truth_crop
    mse = float(np.mean(difference**2))
    rmse = float(np.sqrt(mse))
    psnr = float(20.0 * np.log10(1.0 / max(rmse, 1.0e-12)))
    denominator = float(np.sqrt(np.mean(truth_crop**2)))
    relative_error = 100.0 * rmse / max(denominator, 1.0e-12)

    gy, gx = np.gradient(truth_crop)
    edge_strength = np.hypot(gx, gy)
    threshold = max(0.025, float(np.percentile(edge_strength, 96)))
    strong_edges = edge_strength >= threshold
    if np.any(strong_edges):
        near_edges = ndimage.binary_dilation(strong_edges, iterations=5)
        guard = ndimage.binary_dilation(strong_edges, iterations=1)
        ring_zone = near_edges & ~guard
    else:
        ring_zone = np.zeros_like(strong_edges)

    residual_highpass = ndimage.laplace(difference, mode="reflect")
    if np.any(ring_zone):
        ringing_rms = float(np.sqrt(np.mean(residual_highpass[ring_zone] ** 2)))
        overshoot = np.maximum(np.abs(estimate_crop[ring_zone]) - np.abs(truth_crop[ring_zone]), 0.0)
        overshoot_pct = 100.0 * float(np.percentile(overshoot, 95))
    else:
        ringing_rms = 0.0
        overshoot_pct = 0.0
    ringing_pct = 100.0 * ringing_rms

    ringing_map = np.zeros_like(truth)
    local_map = np.abs(residual_highpass)
    ringing_map[valid][ring_zone] = local_map[ring_zone]
    return ReconstructionMetrics(
        psnr_db=psnr,
        relative_error_pct=relative_error,
        rmse=rmse,
        ringing_index_pct=ringing_pct,
        edge_overshoot_pct=overshoot_pct,
        ringing_map=ringing_map,
        valid_slice=valid,
    )


def structure_observability(image: np.ndarray) -> tuple[float, dict[str, float]]:
    """A truth-free proxy for how strongly image structure constrains blur."""
    gy, gx = np.gradient(image)
    gradient_mean = float(np.mean(np.hypot(gx, gy)))
    jxx = ndimage.gaussian_filter(gx * gx, 3.0, mode="reflect")
    jyy = ndimage.gaussian_filter(gy * gy, 3.0, mode="reflect")
    jxy = ndimage.gaussian_filter(gx * gy, 3.0, mode="reflect")
    trace = jxx + jyy
    determinant = jxx * jyy - jxy * jxy
    local_coherence = np.sqrt(
        np.maximum(trace * trace - 4.0 * determinant, 0.0)
    ) / np.maximum(trace, 1.0e-12)
    coherence = float(np.mean(local_coherence))
    normalized_gradient = float(np.clip((gradient_mean - 0.009) / 0.032, 0.0, 1.0))
    normalized_coherence = float(np.clip(coherence / 0.80, 0.0, 1.0))
    score = float(normalized_gradient * normalized_coherence)
    return score, {
        "gradient_mean": gradient_mean,
        "local_structure_coherence": coherence,
        "normalized_gradient": normalized_gradient,
        "normalized_coherence": normalized_coherence,
    }
