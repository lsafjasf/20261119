"""Matplotlib diagnostics for the blind deconvolution experiment."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

if TYPE_CHECKING:
    from .experiment import CaseResult


def _kernel_snapshots(result: "CaseResult", count: int = 6) -> list[tuple[int, np.ndarray]]:
    history = result.solve.kernel_history
    iterations = result.solve.iteration_history
    if len(history) <= count:
        return list(zip(iterations, history))
    indices = np.linspace(0, len(history) - 1, count).round().astype(int)
    return [(iterations[i], history[i]) for i in indices]


def plot_case_diagnostics(result: "CaseResult", path: Path, dpi: int = 150) -> None:
    from matplotlib.gridspec import GridSpec

    fig = plt.figure(figsize=(14.5, 9.5))
    grid = GridSpec(
        3,
        6,
        figure=fig,
        height_ratios=(1.0, 0.82, 0.86),
        hspace=0.25,
        wspace=0.10,
    )
    top = [
        (result.spec.image, "Sharp reference\n(evaluation only)", "gray", 0, 1),
        (result.observed, "Solver input\n(blurred + noise)", "gray", 0, 1),
        (result.solve.restored, "Alternating estimate", "gray", 0, 1),
        (np.abs(result.solve.restored - result.spec.image), "Absolute error", "magma", None, None),
        (result.reconstruction.ringing_map, "Ringing residual\n(high-pass)", "inferno", None, None),
    ]
    for column, (image, title, cmap, low, high) in enumerate(top):
        axis = fig.add_subplot(grid[0, column])
        axis.imshow(image, cmap=cmap)
        if low is not None:
            axis.imshow(image, cmap=cmap, vmin=low, vmax=high)
        axis.set_title(title, fontsize=8)
        axis.axis("off")

    kernel_uncertainty = result.uncertainty.std_kernel
    kernels = [
        (result.truth_kernel, "Truth kernel (score only)", "magma"),
        (result.solve.initial_kernel, "Truth-blind Gaussian init", "magma"),
        (result.solve.estimated_kernel, "Estimated kernel", "magma"),
        (result.uncertainty.mean_kernel, "Ensemble mean kernel", "magma"),
        (kernel_uncertainty, "Uncertainty envelope", "cividis"),
    ]
    for column, (image, title, cmap) in enumerate(kernels):
        axis = fig.add_subplot(grid[1, column])
        axis.imshow(image, cmap=cmap, interpolation="nearest")
        axis.set_title(title, fontsize=9)
        axis.axis("off")
        if column == 4:
            axis.text(
                0.02,
                -0.08,
                f"{result.uncertainty.label.upper()} uncertainty",
                transform=axis.transAxes,
                fontsize=9,
                color="#b42318" if result.uncertainty.label == "high" else "#344054",
            )

    objective_axis = fig.add_subplot(grid[2, :2])
    iterations = np.arange(1, len(result.solve.objective) + 1)
    objective_axis.semilogy(iterations, result.solve.objective, color="#175cd3", lw=1.8, label="objective")
    objective_axis.semilogy(iterations, result.solve.data_term, color="#dc6803", lw=1.3, label="data term")
    objective_axis.set_title("Objective evolution", fontsize=10)
    objective_axis.set_xlabel("alternating iteration")
    objective_axis.grid(alpha=0.25)
    objective_axis.legend(fontsize=8, frameon=False)

    for index, (iteration, kernel) in enumerate(_kernel_snapshots(result, count=4)):
        axis = fig.add_subplot(grid[2, 2 + index])
        axis.imshow(kernel, cmap="magma", interpolation="nearest")
        axis.set_title(f"kernel @ {iteration}", fontsize=8)
        axis.axis("off")

    fig.suptitle(
        f"{result.spec.label}  |  position error {result.kernel.centroid_error_px:.2f} px"
        f"  |  PSNR {result.reconstruction.psnr_db:.2f} dB"
        f"  |  NCC {result.kernel.ncc:.2f}"
        f"  |  ringing {result.reconstruction.ringing_index_pct:.1f}%",
        fontsize=12,
        y=0.98,
    )
    fig.text(
        0.5,
        0.005,
        "The solver received only the blurred image and max kernel size. "
        "Ground-truth panels are used after estimation for scoring only.",
        ha="center",
        fontsize=8,
        color="#475467",
    )
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def plot_summary(
    results: list["CaseResult"], objective_path: Path, evolution_path: Path, dpi: int = 150
) -> None:
    fig, axes = plt.subplots(2, 3, figsize=(12.5, 6.2), sharex=True)
    for axis, result in zip(axes.ravel(), results):
        iterations = np.arange(1, len(result.solve.objective) + 1)
        axis.semilogy(iterations, result.solve.objective, color="#175cd3", lw=1.5)
        axis.semilogy(iterations, result.solve.data_term, color="#dc6803", lw=1.1)
        axis.set_title(result.spec.label, fontsize=9)
        axis.grid(alpha=0.25)
    fig.suptitle("Objective (blue) and data term (orange)", fontsize=12)
    fig.text(0.5, 0.01, "iteration", ha="center")
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(objective_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(len(results), 6, figsize=(12.0, 2.0 * len(results)))
    for row, result in enumerate(results):
        snapshots = _kernel_snapshots(result, count=6)
        for column, (iteration, kernel) in enumerate(snapshots):
            axis = axes[row, column]
            axis.imshow(kernel, cmap="magma", interpolation="nearest")
            if row == 0:
                axis.set_title(f"kernel @ {iteration}", fontsize=8)
            if column == 0:
                axis.set_ylabel(result.spec.key.replace("_", " "), fontsize=7)
            axis.set_xticks([])
            axis.set_yticks([])
    fig.suptitle("Kernel evolution (truth-blind initialization to final estimate)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(evolution_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def plot_uncertainty_overview(results: list["CaseResult"], path: Path, dpi: int = 150) -> None:
    fig = plt.figure(figsize=(13.0, 9.0))
    for row, result in enumerate(results):
        axis = fig.add_subplot(len(results), 4, 4 * row + 1)
        axis.imshow(result.observed, cmap="gray")
        axis.set_ylabel(result.spec.key.replace("_", " "), fontsize=7)
        axis.set_xticks([])
        axis.set_yticks([])
        if row == 0:
            axis.set_title("observation", fontsize=9)

        axis = fig.add_subplot(len(results), 4, 4 * row + 2)
        axis.imshow(result.uncertainty.mean_kernel, cmap="magma")
        axis.set_xticks([])
        axis.set_yticks([])
        if row == 0:
            axis.set_title("ensemble mean kernel", fontsize=9)

        axis = fig.add_subplot(len(results), 4, 4 * row + 3)
        axis.imshow(result.uncertainty.std_kernel, cmap="cividis")
        axis.set_xticks([])
        axis.set_yticks([])
        if row == 0:
            axis.set_title("uncertainty envelope", fontsize=9)

        axis = fig.add_subplot(len(results), 4, 4 * row + 4)
        color = {"low": "#027a48", "medium": "#dc6803", "high": "#b42318"}[
            result.uncertainty.label
        ]
        axis.barh([0], [result.uncertainty.uncertainty_score], color=color, height=0.36)
        axis.set_xlim(0, 1)
        axis.set_yticks([])
        axis.set_xlabel(
            f"{result.uncertainty.label.upper()}  score={result.uncertainty.uncertainty_score:.2f}",
            fontsize=7,
            color=color,
        )
        axis.tick_params(axis="x", labelsize=6)
        axis.grid(axis="x", alpha=0.25)
    fig.suptitle(
        "Kernel uncertainty: multi-start spread plus an unidentifiability floor",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
