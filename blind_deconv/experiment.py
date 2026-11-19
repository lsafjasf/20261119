"""Experiment driver with an explicit truth/solver boundary."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .metrics import (
    KernelMetrics,
    ReconstructionMetrics,
    compute_kernel_metrics,
    compute_reconstruction_metrics,
)
from .samples import SampleSpec, build_sample_specs, make_observations
from .solver import BlindDeconvolutionSolver, SolveResult, SolverConfig
from .trajectory import kernel_moments
from .uncertainty import UncertaintyResult, estimate_kernel_uncertainty
from .visualize import plot_case_diagnostics, plot_summary, plot_uncertainty_overview


@dataclass
class CaseResult:
    spec: SampleSpec
    truth_kernel: np.ndarray
    clean_blur: np.ndarray
    observed: np.ndarray
    solve: SolveResult
    uncertainty: UncertaintyResult
    kernel: KernelMetrics
    reconstruction: ReconstructionMetrics
    blurred_baseline: ReconstructionMetrics
    elapsed_seconds: float


@dataclass
class ExperimentConfig:
    output_dir: Path = Path("results")
    image_size: int = 192
    max_kernel_size: int = 21
    ensemble_members: int = 5
    solver: SolverConfig = field(default_factory=SolverConfig)
    dpi: int = 150


def _metric_dict(metrics: ReconstructionMetrics) -> dict[str, float]:
    return {
        "psnr_db": round(metrics.psnr_db, 6),
        "relative_error_pct": round(metrics.relative_error_pct, 6),
        "rmse": round(metrics.rmse, 8),
        "ringing_index_pct": round(metrics.ringing_index_pct, 6),
        "edge_overshoot_pct": round(metrics.edge_overshoot_pct, 6),
    }


def _kernel_evolution_stats(result: SolveResult) -> list[dict[str, float | int]]:
    stats: list[dict[str, float | int]] = []
    for iteration, kernel in zip(result.iteration_history, result.kernel_history):
        cy, cx = kernel_moments(kernel)
        nonzero = kernel[kernel > 0]
        entropy = float(-np.sum(nonzero * np.log(nonzero)))
        stats.append(
            {
                "iteration": int(iteration),
                "centroid_y": round(cy, 5),
                "centroid_x": round(cx, 5),
                "mass": round(float(kernel.sum()), 8),
                "support_pixels": int(np.count_nonzero(kernel > 1.0e-6)),
                "entropy": round(entropy, 6),
                "peak": round(float(kernel.max()), 8),
            }
        )
    return stats


def _case_json(result: CaseResult, figure_name: str) -> dict[str, object]:
    uncertainty = result.uncertainty
    return {
        "sample": {
            "key": result.spec.key,
            "label": result.spec.label,
            "category": result.spec.category,
            "notes": result.spec.notes,
            "truth_trajectory_xy": [list(point) for point in result.spec.trajectory],
            "truth_kernel_size": result.spec.truth_kernel_size,
            "observation_noise_sigma": result.spec.noise_sigma,
        },
        "solver_input": {
            "shape": list(result.observed.shape),
            "max_kernel_size": int(result.solve.initial_kernel.shape[0]),
            "initial_kernel_source": result.solve.metadata["initial_kernel_source"],
            "truth_used_by_solver": False,
        },
        "kernel_metrics": {
            "centroid_error_px": round(result.kernel.centroid_error_px, 6),
            "peak_error_px": round(result.kernel.peak_error_px, 6),
            "ncc": round(result.kernel.ncc, 6),
            "truth_centroid_yx": [round(v, 6) for v in result.kernel.truth_centroid_yx],
            "estimate_centroid_yx": [round(v, 6) for v in result.kernel.estimate_centroid_yx],
            "truth_peak_yx": list(result.kernel.truth_peak_yx),
            "estimate_peak_yx": list(result.kernel.estimate_peak_yx),
        },
        "reconstruction": _metric_dict(result.reconstruction),
        "blurred_baseline": _metric_dict(result.blurred_baseline),
        "uncertainty": {
            "label": uncertainty.label,
            "score": round(uncertainty.uncertainty_score, 6),
            "observability": round(uncertainty.observability, 6),
            "centroid_std_px": round(uncertainty.centroid_std_px, 6),
            "ensemble_centroid_std_px": round(
                uncertainty.ensemble_centroid_std_px, 6
            ),
            "unidentifiability_floor_px": round(uncertainty.uncertainty_floor_px, 6),
            "centroid_95pct_span_yx": [
                round(uncertainty.centroid_interval_yx[0], 6),
                round(uncertainty.centroid_interval_yx[1], 6),
            ],
            "relative_kernel_std": round(uncertainty.relative_kernel_std, 6),
            "ensemble_size": int(uncertainty.kernels.shape[0]),
            "diagnostics": {
                key: round(float(value), 8) for key, value in uncertainty.diagnostics.items()
            },
        },
        "objective": {
            "iteration": list(range(1, len(result.solve.objective) + 1)),
            "objective": [round(float(v), 10) for v in result.solve.objective],
            "data_term": [round(float(v), 10) for v in result.solve.data_term],
            "regularizer": [round(float(v), 10) for v in result.solve.regularizer],
        },
        "kernel_evolution_stats": _kernel_evolution_stats(result.solve),
        "runtime_seconds": round(result.elapsed_seconds, 4),
        "figure": figure_name,
    }


def run_experiment(config: ExperimentConfig | None = None, *, verbose: bool = True) -> list[CaseResult]:
    cfg = config or ExperimentConfig()
    output_dir = cfg.output_dir.resolve()
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    solver = BlindDeconvolutionSolver(cfg.solver)
    specs = build_sample_specs(cfg.image_size)
    results: list[CaseResult] = []

    for spec in specs:
        started = time.perf_counter()
        truth_kernel, clean_blur, observed = make_observations(spec, cfg.max_kernel_size)

        # The solver's entire observation is the next line: blurred pixels plus a size cap.
        solve = solver.solve(observed, cfg.max_kernel_size)
        uncertainty = estimate_kernel_uncertainty(
            observed,
            cfg.max_kernel_size,
            cfg.solver,
            members=cfg.ensemble_members,
        )
        kernel_metrics = compute_kernel_metrics(truth_kernel, solve.estimated_kernel)
        reconstruction = compute_reconstruction_metrics(
            solve.restored, spec.image, margin=4 * cfg.max_kernel_size
        )
        blurred_baseline = compute_reconstruction_metrics(
            observed, spec.image, margin=4 * cfg.max_kernel_size
        )
        result = CaseResult(
            spec=spec,
            truth_kernel=truth_kernel,
            clean_blur=clean_blur,
            observed=observed,
            solve=solve,
            uncertainty=uncertainty,
            kernel=kernel_metrics,
            reconstruction=reconstruction,
            blurred_baseline=blurred_baseline,
            elapsed_seconds=time.perf_counter() - started,
        )
        plot_case_diagnostics(result, figures_dir / f"{spec.key}.png", dpi=cfg.dpi)
        results.append(result)
        if verbose:
            print(
                f"[{spec.key}] pos={kernel_metrics.centroid_error_px:.2f}px "
                f"PSNR={reconstruction.psnr_db:.2f}dB "
                f"ring={reconstruction.ringing_index_pct:.1f}% "
                f"uncertainty={uncertainty.label} ({uncertainty.uncertainty_score:.2f})"
            )

    plot_summary(
        results,
        figures_dir / "objective_curves.png",
        figures_dir / "kernel_evolution.png",
        dpi=cfg.dpi,
    )
    plot_uncertainty_overview(results, figures_dir / "uncertainty_overview.png", dpi=cfg.dpi)
    _write_outputs(results, output_dir, cfg)
    return results


def _write_outputs(
    results: list[CaseResult], output_dir: Path, cfg: ExperimentConfig
) -> None:
    from .report import write_html_report

    payload = {
        "experiment": {
            "image_size": cfg.image_size,
            "max_kernel_size": cfg.max_kernel_size,
            "ensemble_members": cfg.ensemble_members,
            "solver_config": {
                "max_iterations": cfg.solver.max_iterations,
                "inner_kernel_steps": cfg.solver.inner_kernel_steps,
                "image_reg": cfg.solver.image_reg,
                "image_relax": cfg.solver.image_relax,
                "init_sigma": cfg.solver.init_sigma,
                "kernel_step": cfg.solver.kernel_step,
                "constraints": "non-negative kernel; sum(kernel)=1",
            },
            "solver_boundary": (
                "The estimator receives only the blurred image and max kernel size. "
                "Truth trajectories are used after estimation for metrics and figures."
            ),
        },
        "cases": [
            _case_json(result, f"figures/{result.spec.key}.png") for result in results
        ],
    }
    with (output_dir / "results.json").open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    _write_csv(results, output_dir / "metrics.csv")
    write_html_report(results, payload, output_dir / "report.html")


def _write_csv(results: list[CaseResult], path: Path) -> None:
    lines = [
        "sample,category,kernel_centroid_error_px,kernel_peak_error_px,kernel_ncc,"
        "reconstruction_psnr_db,reconstruction_relative_error_pct,"
        "ringing_index_pct,edge_overshoot_pct,uncertainty_label,uncertainty_score,"
        "blurred_psnr_db"
    ]
    for result in results:
        lines.append(
            f"{result.spec.key},{result.spec.category},"
            f"{result.kernel.centroid_error_px:.6f},{result.kernel.peak_error_px:.6f},"
            f"{result.kernel.ncc:.6f},{result.reconstruction.psnr_db:.6f},"
            f"{result.reconstruction.relative_error_pct:.6f},"
            f"{result.reconstruction.ringing_index_pct:.6f},"
            f"{result.reconstruction.edge_overshoot_pct:.6f},"
            f"{result.uncertainty.label},{result.uncertainty.uncertainty_score:.6f},"
            f"{result.blurred_baseline.psnr_db:.6f}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
