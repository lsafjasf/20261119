"""Figures and tiled frames for the denoising experiment."""

import os
import numpy as np
import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .metrics import per_frame_psnr


def _tile(frames, cols=4):
    rows = int(np.ceil(len(frames) / cols))
    h, w = frames[0].shape
    canvas = np.zeros((rows * h, cols * w), np.float32)
    for i, f in enumerate(frames):
        r, c = divmod(i, cols)
        canvas[r * h:(r + 1) * h, c * w:(c + 1) * w] = f
    return canvas


def _save_gray(path, img, cmap="gray"):
    plt.imsave(path, np.clip(img, 0, 1), cmap=cmap, vmin=0, vmax=1)


def psnr_curves(d, res, outdir):
    gt = d["gt"]
    fig, ax = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    for name, seq, col in [("noisy", d["noisy"], "tab:red"),
                           ("denoised", res["out"], "tab:blue")]:
        ax[0].plot(per_frame_psnr(gt, seq, d["static"]),
                   label=name, color=col, alpha=0.9)
        ax[1].plot(per_frame_psnr(gt, seq, d["motion"]),
                   label=name, color=col, alpha=0.9)
    for a, title in zip(ax, ["Static region PSNR", "Motion region PSNR"]):
        a.axvline(d["cut"], color="k", ls="--", lw=1, label="scene cut")
        a.set_ylabel("PSNR (dB)")
        a.set_title(title)
        a.grid(alpha=0.3)
        a.legend()
    ax[1].set_xlabel("frame")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "psnr_curves.png"), dpi=130)
    plt.close(fig)


def weight_figure(d, res, outdir):
    fig, ax = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    t = np.arange(d["gt"].shape[0])
    sw = [res["weights"][i][d["static"][i] > 0].mean()
          if d["static"][i].sum() else np.nan for i in t]
    mw = [res["weights"][i][d["motion"][i] > 0].mean()
          if d["motion"][i].sum() else np.nan for i in t]
    ax[0].plot(t, sw, label="static mean weight", color="tab:green")
    ax[0].plot(t, mw, label="motion mean weight", color="tab:orange")
    ax[0].plot(t, res["n_eff"], label="effective averaged frames N",
               color="tab:purple", ls="--")
    ax[0].axvline(d["cut"], color="k", ls="--", lw=1)
    ax[0].set_ylabel("history weight / N")
    ax[0].set_title("Temporal history weights")
    ax[0].grid(alpha=0.3)
    ax[0].legend()

    wt = res["weights"].reshape(res["weights"].shape[0], -1).T
    im = ax[1].imshow(wt, aspect="auto", cmap="magma",
                      vmin=0, vmax=1, origin="lower")
    ax[1].axvline(d["cut"], color="w", ls="--", lw=1)
    ax[1].set_xlabel("frame")
    ax[1].set_ylabel("pixel row")
    ax[1].set_title("Per-pixel history weight over time")
    fig.colorbar(im, ax=ax[1])
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "history_weights.png"), dpi=130)
    plt.close(fig)

    frames = [res["weights"][i] for i in [2, 8, 16, 22, 26, 32, 40, 47]]
    _save_gray(os.path.join(outdir, "weight_maps_tile.png"),
               _tile(frames), cmap="magma")


def cut_figure(d, res_noreset, res, outdir):
    cut = d["cut"]
    rows = [
        ("ground truth", d["gt"][cut - 1:cut + 2]),
        ("noisy input", d["noisy"][cut - 1:cut + 2]),
        ("no cut reset (ghosting)", res_noreset["out"][cut - 1:cut + 2]),
        ("with cut reset (ours)", res["out"][cut - 1:cut + 2]),
    ]
    fig, ax = plt.subplots(4, 3, figsize=(11, 12))
    for r, (name, frames) in enumerate(rows):
        for c, f in enumerate(frames):
            ax[r, c].imshow(f, cmap="gray", vmin=0, vmax=1)
            ax[r, c].axis("off")
            if c == 0:
                ax[r, c].set_ylabel(name, fontsize=10)
            if r == 0:
                ax[r, c].set_title(f"frame {cut - 1 + c}")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "scene_cut.png"), dpi=130)
    plt.close(fig)


def panel_figure(d, res, outdir):
    pick = [4, 12, 20, 30, 40]
    rows = [("GT", d["gt"]), ("noisy", d["noisy"]),
            ("denoised", res["out"]), ("history weight", res["weights"])]
    fig, ax = plt.subplots(4, len(pick), figsize=(13, 10))
    for r, (name, seq) in enumerate(rows):
        for c, t in enumerate(pick):
            cmap = "magma" if name == "history weight" else "gray"
            vmax = 1.0
            ax[r, c].imshow(seq[t], cmap=cmap, vmin=0, vmax=vmax)
            ax[r, c].axis("off")
            if r == 0:
                ax[r, c].set_title(f"frame {t}")
            if c == 0:
                ax[r, c].set_ylabel(name, fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "panel.png"), dpi=130)
    plt.close(fig)


def noise_figure(d, est_sigma, outdir):
    t = np.arange(d["gt"].shape[0])
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(t, est_sigma * 255, label="Immerkaer estimate", color="tab:blue")
    ax.axhline(d["sigma"] * 255, color="tab:red", ls="--",
               label=f"true sigma = {d['sigma'] * 255:.1f} levels")
    actual = np.array([(d["noisy"][i] - d["gt"][i]).std() for i in t]) * 255
    ax.plot(t, actual, color="tab:green", lw=1, alpha=0.7,
            label="realized residual std")
    ax.axvline(d["cut"], color="k", ls=":", lw=1, label="scene cut")
    ax.set_xlabel("frame")
    ax.set_ylabel("noise std (0-255 levels)")
    ax.set_title("Noise estimation per frame")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "noise_estimate.png"), dpi=130)
    plt.close(fig)


def save_tiles_and_video(d, res, outdir):
    pick = list(range(0, d["gt"].shape[0], 4))
    _save_gray(os.path.join(outdir, "tile_gt.png"), _tile([d["gt"][i] for i in pick]))
    _save_gray(os.path.join(outdir, "tile_noisy.png"),
               _tile([d["noisy"][i] for i in pick]))
    _save_gray(os.path.join(outdir, "tile_denoised.png"),
               _tile([res["out"][i] for i in pick]))
    _save_gray(os.path.join(outdir, "tile_weights.png"),
               _tile([res["weights"][i] for i in pick]), cmap="magma")

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    h, w = d["gt"][0].shape
    vw = cv2.VideoWriter(os.path.join(outdir, "comparison.mp4"),
                         fourcc, 8, (w * 3, h), False)
    for t in range(d["gt"].shape[0]):
        frame = np.concatenate([
            (d["noisy"][t] * 255).astype(np.uint8),
            (res["out"][t] * 255).astype(np.uint8),
            (res["weights"][t] * 255).astype(np.uint8)], axis=1)
        vw.write(frame)
    vw.release()
