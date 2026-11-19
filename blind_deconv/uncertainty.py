"""Kernel uncertainty from truth-blind multi-start optimization."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .metrics import structure_observability
from .solver import SolverConfig, blindly_deconvolve
from .trajectory import kernel_moments


@dataclass
class UncertaintyResult:
    kernels: np.ndarray
    mean_kernel: np.ndarray
    std_kernel: np.ndarray
    ensemble_std_kernel: np.ndarray
    centroid_std_px: float
    ensemble_centroid_std_px: float
    uncertainty_floor_px: float
    relative_kernel_std: float
    centroid_interval_yx: tuple[float, float]
    observability: float
    uncertainty_score: float
    label: str
    diagnostics: dict[str, float]


def _member_configs(base: SolverConfig, members: int) -> list[SolverConfig]:
    variants = (
        (0.78, 0.72, 0.72, 0.90),
        (1.00, 1.00, 1.00, 1.00),
        (1.24, 1.35, 1.18, 0.82),
        (0.90, 1.70, 1.45, 1.10),
        (1.12, 0.82, 0.86, 0.72),
        (1.35, 1.15, 1.25, 1.20),
    )
    configs: list[SolverConfig] = []
    for index in range(max(2, members)):
        sigma_scale, reg_scale, relax_scale, step_scale = variants[index % len(variants)]
        configs.append(
            SolverConfig(
                max_iterations=base.max_iterations,
                inner_kernel_steps=base.inner_kernel_steps,
                image_reg=base.image_reg * reg_scale,
                image_reg_decay=base.image_reg_decay,
                image_relax=float(np.clip(base.image_relax * relax_scale, 0.08, 0.85)),
                gradient_weight=base.gradient_weight,
                kernel_step=base.kernel_step * step_scale,
                kernel_smooth=base.kernel_smooth,
                prune_ratio=base.prune_ratio,
                init_sigma=base.init_sigma * sigma_scale,
                tv_weight=base.tv_weight,
                entropy_weight=base.entropy_weight,
                snapshot_every=base.snapshot_every,
                seed=base.seed + index,
            )
        )
    return configs


def estimate_kernel_uncertainty(
    blurred: np.ndarray,
    max_kernel_size: int,
    base_config: SolverConfig,
    *,
    members: int = 5,
) -> UncertaintyResult:
    """Run a deterministic multi-start ensemble using only the observation."""
    kernels = [
        blindly_deconvolve(blurred, max_kernel_size, config).estimated_kernel
        for config in _member_configs(base_config, members)
    ]
    stack = np.stack(kernels, axis=0)
    mean_kernel = stack.mean(axis=0)
    ensemble_std_kernel = stack.std(axis=0)
    centroids = np.asarray([kernel_moments(kernel) for kernel in stack])
    ensemble_centroid_std = float(
        np.sqrt(np.var(centroids[:, 0]) + np.var(centroids[:, 1]))
    )
    relative_std = float(
        np.mean(ensemble_std_kernel) / max(float(np.mean(mean_kernel)), 1.0e-12)
    )
    spread_y = float(np.percentile(centroids[:, 0], 95) - np.percentile(centroids[:, 0], 5))
    spread_x = float(np.percentile(centroids[:, 1], 95) - np.percentile(centroids[:, 1], 5))
    observability, structure_diag = structure_observability(blurred)
    uncertainty_floor = float((1.0 - observability) * max_kernel_size * 0.12)
    centroid_std = float(
        np.hypot(ensemble_centroid_std, uncertainty_floor)
    )
    if uncertainty_floor > 1.0e-6:
        yy, xx = np.mgrid[:max_kernel_size, :max_kernel_size]
        center = (max_kernel_size - 1) / 2.0
        floor_map = np.exp(
            -0.5 * ((yy - center) ** 2 + (xx - center) ** 2) / uncertainty_floor**2
        )
        floor_map /= floor_map.sum()
        floor_map *= max(float(mean_kernel.sum()), 1.0e-12)
        std_kernel = np.hypot(ensemble_std_kernel, floor_map)
    else:
        std_kernel = ensemble_std_kernel
    ensemble_ambiguity = float(np.clip(relative_std / 1.4, 0.0, 1.0))
    score = float(
        np.clip(0.72 * (1.0 - observability) + 0.28 * ensemble_ambiguity, 0.0, 1.0)
    )
    if score >= 0.62:
        label = "high"
    elif score >= 0.25:
        label = "medium"
    else:
        label = "low"
    diagnostics = {
        **structure_diag,
        "ensemble_ambiguity": ensemble_ambiguity,
        "centroid_std_px": centroid_std,
        "ensemble_centroid_std_px": ensemble_centroid_std,
        "uncertainty_floor_px": uncertainty_floor,
        "relative_kernel_std": relative_std,
        "centroid_95pct_span_y": spread_y,
        "centroid_95pct_span_x": spread_x,
        "ensemble_size": float(stack.shape[0]),
    }
    return UncertaintyResult(
        kernels=stack,
        mean_kernel=mean_kernel,
        std_kernel=std_kernel,
        ensemble_std_kernel=ensemble_std_kernel,
        centroid_std_px=centroid_std,
        ensemble_centroid_std_px=ensemble_centroid_std,
        uncertainty_floor_px=uncertainty_floor,
        relative_kernel_std=relative_std,
        centroid_interval_yx=(
            spread_y + 2.0 * uncertainty_floor,
            spread_x + 2.0 * uncertainty_floor,
        ),
        observability=observability,
        uncertainty_score=score,
        label=label,
        diagnostics=diagnostics,
    )
