"""Truth-blind alternating blind deconvolution.

The public solver accepts only a blurred image and an upper bound on kernel
size. Ground-truth trajectories and sharp images are not parameters of this
module's estimation path.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from .trajectory import centered_gaussian_on_canvas


@dataclass(frozen=True)
class SolverConfig:
    max_iterations: int = 24
    inner_kernel_steps: int = 3
    image_reg: float = 0.02
    image_reg_decay: float = 0.94
    image_relax: float = 0.42
    gradient_weight: float = 0.35
    kernel_step: float = 0.03
    kernel_smooth: float = 0.25
    prune_ratio: float = 0.012
    init_sigma: float = 2.5
    tv_weight: float = 2.5e-4
    entropy_weight: float = 2.0e-4
    snapshot_every: int = 4
    seed: int = 0


@dataclass
class SolveResult:
    estimated_kernel: np.ndarray
    restored: np.ndarray
    initial_kernel: np.ndarray
    objective: list[float]
    data_term: list[float]
    regularizer: list[float]
    kernel_history: list[np.ndarray]
    image_history: list[np.ndarray]
    iteration_history: list[int]
    config: SolverConfig
    metadata: dict[str, object] = field(default_factory=dict)


def _odd_upper_bound(value: int) -> int:
    if value < 3:
        raise ValueError("max_kernel_size must be >= 3")
    return value if value % 2 == 1 else value - 1


def _centered_kernel_fft(kernel: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    sized = np.zeros(shape, dtype=np.float64)
    h, w = kernel.shape
    sized[:h, :w] = kernel
    sized = np.roll(sized, shift=(-(h // 2), -(w // 2)), axis=(0, 1))
    return np.fft.rfft2(sized)


def _deconvolve(image: np.ndarray, kernel: np.ndarray, reg: float) -> np.ndarray:
    kh = _centered_kernel_fft(kernel, image.shape)
    bh = np.fft.rfft2(image)
    fy = np.fft.fftfreq(image.shape[0])[:, None]
    fx = np.fft.rfftfreq(image.shape[1])[None, :]
    highpass = (2.0 - 2.0 * np.cos(2.0 * np.pi * fy)) ** 2
    highpass = highpass + (2.0 - 2.0 * np.cos(2.0 * np.pi * fx)) ** 2
    denominator = np.abs(kh) ** 2 + reg * highpass + 1.0e-8
    restored_h = np.conj(kh) * bh / denominator
    restored = np.fft.irfft2(restored_h, s=image.shape)
    return np.clip(restored, 0.0, 1.0)


def _apply_kernel(image: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    kernel_fft = _centered_kernel_fft(kernel, image.shape)
    return np.fft.irfft2(
        np.fft.rfft2(image) * kernel_fft,
        s=image.shape,
    )


def _correlate_center(
    image: np.ndarray, residual: np.ndarray, kernel_size: int
) -> np.ndarray:
    corr = np.fft.ifft2(np.conj(np.fft.fft2(image)) * np.fft.fft2(residual)).real
    half = kernel_size // 2
    corr = np.roll(corr, shift=(half, half), axis=(0, 1))
    out = corr[:kernel_size, :kernel_size]
    return out / image.size


def _project_simplex(kernel: np.ndarray) -> np.ndarray:
    """Project onto non-negative kernels whose entries sum to one."""
    flat = np.asarray(kernel, dtype=np.float64).ravel()
    if not np.all(np.isfinite(flat)):
        flat = np.nan_to_num(flat, nan=0.0, posinf=0.0, neginf=0.0)
    sorted_values = np.sort(flat)[::-1]
    cumulative = np.cumsum(sorted_values)
    rho_candidates = np.nonzero(
        sorted_values - (cumulative - 1.0) / (np.arange(flat.size) + 1.0) > 0
    )[0]
    rho = int(rho_candidates[-1])
    theta = (cumulative[rho] - 1.0) / (rho + 1.0)
    projected = np.maximum(flat - theta, 0.0)
    projected /= projected.sum()
    return projected.reshape(kernel.shape)


def _kernel_support_prior(kernel: np.ndarray) -> np.ndarray:
    yy, xx = np.mgrid[: kernel.shape[0], : kernel.shape[1]]
    c = (kernel.shape[0] - 1) / 2.0
    radius = np.hypot(yy - c, xx - c)
    taper = np.exp(-0.5 * (radius / (0.47 * kernel.shape[0])) ** 8)
    return taper


def _objective_terms(
    blurred: np.ndarray,
    restored: np.ndarray,
    kernel: np.ndarray,
    config: SolverConfig,
    reg: float,
) -> tuple[float, float, float]:
    prediction = _apply_kernel(restored, kernel)
    residual = prediction - blurred
    data = 0.5 * float(np.mean(residual**2))
    gy, gx = np.gradient(restored)
    image_reg = 0.5 * reg * float(np.mean(gx**2 + gy**2))
    small = np.maximum(kernel, 1.0e-12)
    entropy_penalty = float(
        np.sum(small * np.log(small)) + np.log(kernel.size)
    )
    tv = float(np.sum(np.abs(np.diff(kernel, axis=0))) + np.sum(np.abs(np.diff(kernel, axis=1))))
    regularizer = (
        image_reg + config.entropy_weight * entropy_penalty + config.tv_weight * tv
    )
    return data + regularizer, data, regularizer


def _gaussian_smooth_kernel(kernel: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return kernel
    smoothed = ndimage.gaussian_filter(kernel, sigma=sigma, mode="constant")
    return _project_simplex(smoothed)


def blindly_deconvolve(
    blurred: np.ndarray,
    max_kernel_size: int,
    config: SolverConfig | None = None,
) -> SolveResult:
    """Estimate a non-negative unit-sum kernel and a sharp image.

    Only ``blurred`` and ``max_kernel_size`` affect the estimate. The kernel is
    initialized from a centered isotropic Gaussian, never from a trajectory.
    """
    cfg = config or SolverConfig()
    if blurred.ndim != 2:
        raise ValueError("blurred image must be 2-D")
    if not np.all(np.isfinite(blurred)):
        raise ValueError("blurred image contains non-finite values")
    size = _odd_upper_bound(int(max_kernel_size))

    image = np.clip(blurred.astype(np.float64, copy=True), 0.0, 1.0)
    obs_gy, obs_gx = np.gradient(image)
    observation_gradient = float(np.mean(np.hypot(obs_gx, obs_gy)))
    regularization_scale = 1.0 + 8.0 * max(
        0.0, 1.0 - observation_gradient / 0.03
    ) ** 2
    kernel = centered_gaussian_on_canvas(size, cfg.init_sigma)
    initial_kernel = kernel.copy()
    support_prior = _kernel_support_prior(kernel)

    m = np.zeros_like(kernel)
    v = np.zeros_like(kernel)
    beta1, beta2 = 0.85, 0.995
    objective: list[float] = []
    data_terms: list[float] = []
    regularizers: list[float] = []
    kernel_history: list[np.ndarray] = [kernel.copy()]
    image_history: list[np.ndarray] = [image.copy()]
    iteration_history: list[int] = [0]
    restored = image.copy()

    for iteration in range(1, cfg.max_iterations + 1):
        schedule = (iteration - 1) / max(1, cfg.max_iterations - 1)
        reg = cfg.image_reg * (cfg.image_reg_decay ** (iteration - 1))
        deconvolved = _deconvolve(image, kernel, reg)
        restored = np.clip(
            image + cfg.image_relax * (deconvolved - image), 0.0, 1.0
        )

        target = _apply_kernel(restored, kernel)
        residual = target - image
        gy_r, gx_r = np.gradient(restored)
        gy_b, gx_b = np.gradient(image)
        grad_res_y = _apply_kernel(gy_r, kernel) - gy_b
        grad_res_x = _apply_kernel(gx_r, kernel) - gx_b

        for inner in range(cfg.inner_kernel_steps):
            pos = _correlate_center(restored, residual, size)
            grad_y = _correlate_center(gy_r, grad_res_y, size)
            grad_x = _correlate_center(gx_r, grad_res_x, size)
            gradient = pos + cfg.gradient_weight * (grad_y + grad_x)
            gradient += cfg.tv_weight * ndimage.laplace(kernel, mode="constant")
            small = np.maximum(kernel, 1.0e-12)
            gradient += cfg.entropy_weight * (np.log(small) + 1.0)
            gradient -= float(gradient.mean())
            gradient *= support_prior

            step_index = iteration * cfg.inner_kernel_steps + inner
            m = beta1 * m + (1.0 - beta1) * gradient
            v = beta2 * v + (1.0 - beta2) * gradient**2
            m_hat = m / (1.0 - beta1 ** (step_index + 1))
            v_hat = v / (1.0 - beta2 ** (step_index + 1))
            lr = cfg.kernel_step * (1.0 - 0.55 * schedule)
            kernel = kernel - lr * m_hat / (np.sqrt(v_hat) + 1.0e-10)
            kernel = _gaussian_smooth_kernel(np.maximum(kernel, 0.0), cfg.kernel_smooth)
            cutoff = cfg.prune_ratio * float(kernel.max())
            kernel[kernel < cutoff] = 0.0
            kernel = _project_simplex(kernel * support_prior)

        value, data_value, regularizer = _objective_terms(
            image, restored, kernel, cfg, reg
        )
        objective.append(value)
        data_terms.append(data_value)
        regularizers.append(regularizer)
        kernel_history.append(kernel.copy())
        iteration_history.append(iteration)
        if iteration == 1 or iteration % cfg.snapshot_every == 0 or iteration == cfg.max_iterations:
            image_history.append(restored.copy())

    kernel[kernel < cfg.prune_ratio * float(kernel.max())] = 0.0
    kernel = _project_simplex(kernel)
    restored = _deconvolve(
        image, kernel, max(0.03, 1.5 * cfg.image_reg * regularization_scale)
    )
    restoration_confidence = float(
        np.clip((observation_gradient - 0.008) / 0.025, 0.0, 1.0)
    )
    restored = np.clip(
        image + restoration_confidence * (restored - image), 0.0, 1.0
    )
    final_target = _apply_kernel(restored, kernel)
    final_data = 0.5 * float(np.mean((final_target - image) ** 2))
    metadata: dict[str, object] = {
        "initial_kernel_source": "centered_isometric_gaussian",
        "max_kernel_size": size,
        "final_data_fit": final_data,
        "estimated_support_pixels": int(np.count_nonzero(kernel > 1.0e-5)),
        "observation_gradient_mean": observation_gradient,
        "adaptive_image_reg_scale": regularization_scale,
        "restoration_confidence": restoration_confidence,
        "structure_limited_fallback": bool(restoration_confidence < 0.2),
        "truth_used_by_solver": False,
    }
    return SolveResult(
        estimated_kernel=kernel,
        restored=restored,
        initial_kernel=initial_kernel,
        objective=objective,
        data_term=data_terms,
        regularizer=regularizers,
        kernel_history=kernel_history,
        image_history=image_history,
        iteration_history=iteration_history,
        config=cfg,
        metadata=metadata,
    )


class BlindDeconvolutionSolver:
    """Small truth-blind façade used by the experiment driver."""

    def __init__(self, config: SolverConfig | None = None):
        self.config = config or SolverConfig()

    def solve(self, blurred: np.ndarray, max_kernel_size: int) -> SolveResult:
        return blindly_deconvolve(blurred, max_kernel_size, self.config)
