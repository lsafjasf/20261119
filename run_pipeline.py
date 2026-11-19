"""Generate the synthetic sequence, run the full pipeline and save artefacts."""

import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from hdr_merge.evaluation import (
    ghost_energy,
    motion_iou,
    Report,
    static_relative_error,
    unrecoverable_check,
)
from hdr_merge.merge import merge_exposures
from hdr_merge.synthetic import generate_test_sequence

OUT = os.path.join(os.path.dirname(__file__), "artifacts")


def _tonemap(x):
    x = np.maximum(x, 0)
    return x / (1.0 + x)


def save_figure(seq, result, report):
    fig, axes = plt.subplots(2, 3, figsize=(13, 7))
    axes[0, 0].imshow(seq.images[len(seq.images) // 2])
    axes[0, 0].set_title("middle input frame (moving disk + lamp)")
    axes[0, 1].imshow(_tonemap(result.irradiance), cmap="gray")
    axes[0, 1].set_title("merged linear irradiance (tonemapped)")
    axes[0, 2].imshow(result.valid, cmap="viridis")
    axes[0, 2].set_title("validity mask (green=valid)")
    axes[1, 0].imshow(result.motion_mask, cmap="magma")
    axes[1, 0].set_title("detected motion region")
    axes[1, 1].imshow(seq.motion_gt, cmap="magma")
    axes[1, 1].set_title("ground-truth motion region")
    for c, color in enumerate(["r", "g", "b"]):
        axes[1, 2].plot(seq.crf[:, c], color + "--", label=f"true {color}")
        axes[1, 2].plot(result.g[:, c], color, alpha=0.6, label=f"est {color}")
    axes[1, 2].set_title("inverse response g(z): true vs estimated")
    axes[1, 2].legend(fontsize=7)
    for ax in axes.flat[:-1]:
        ax.axis("off")
    fig.tight_layout()
    path = os.path.join(OUT, "pipeline_overview.png")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def main():
    os.makedirs(OUT, exist_ok=True)
    seq = generate_test_sequence()
    result = merge_exposures(seq.images, seq.exposures)

    kept_images = np.delete(seq.images, result.rejected, axis=0)
    kept_exposures = np.delete(seq.exposures, result.rejected)
    static, _ = static_relative_error(
        result.irradiance, seq.irradiance, seq.static_mask, result.valid
    )
    ghost = ghost_energy(
        kept_images, kept_exposures, result.g, result.irradiance,
        seq.motion_gt, result.reference, result.motion_mask, result.valid,
    )
    clipping = unrecoverable_check(kept_images, result.valid)
    report = Report(
        static=static,
        ghost=ghost,
        clipping=clipping,
        rejected_frames=result.rejected.tolist(),
        outlier_detected=seq.outlier_index in result.rejected.tolist(),
        motion_iou=motion_iou(result.motion_mask, seq.motion_gt),
    )

    np.save(os.path.join(OUT, "irradiance.npy"), result.irradiance)
    np.save(os.path.join(OUT, "valid_mask.npy"), result.valid)
    np.savez(
        os.path.join(OUT, "sequence.npz"),
        images=seq.images,
        exposures=seq.exposures,
        ground_truth=seq.irradiance,
        motion_gt=seq.motion_gt,
    )
    with open(os.path.join(OUT, "report.json"), "w") as f:
        json.dump(report.as_dict(), f, indent=2)
    fig_path = save_figure(seq, result, report)

    print(json.dumps(report.as_dict(), indent=2))
    print("artefacts written to", OUT)
    print("figure:", fig_path)


if __name__ == "__main__":
    main()
