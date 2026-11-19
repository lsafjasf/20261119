"""End-to-end driver: synthetic data -> denoising -> metrics -> artifacts."""

import os
import json
import numpy as np

from .synth import build, SIGMA
from .denoiser import denoise_sequence, immerkaer_sigma
from .metrics import all_psnr, noise_std, hf_fidelity
from . import visualize

ART = os.path.join(os.path.dirname(__file__), "..", "artifacts")


def main():
    os.makedirs(ART, exist_ok=True)
    d = build()
    gt, noisy = d["gt"], d["noisy"]

    est = np.array([immerkaer_sigma(noisy[t]) for t in range(noisy.shape[0])])
    noise = {
        "true_sigma_01": float(SIGMA),
        "true_sigma_levels_0_255": float(SIGMA * 255),
        "residual_sigma_01": noise_std(noisy, gt),
        "immerkaer_mean_01": float(est.mean()),
        "immerkaer_std_01": float(est.std()),
        "immerkaer_mean_levels_0_255": float(est.mean() * 255),
    }

    res = denoise_sequence(noisy)
    res_nomc = denoise_sequence(noisy, use_mc=False)
    res_noreset = denoise_sequence(noisy, reset_on_cut=False)

    table = [
        all_psnr(d, noisy, "noisy input"),
        all_psnr(d, res_nomc["out"], "temporal filter, no motion comp."),
        all_psnr(d, res_noreset["out"], "motion comp., no cut reset"),
        all_psnr(d, res["out"], "motion comp. + cut reset (ours)"),
    ]

    # Cut-frame quality: does the new scene stay clean at t = cut?
    cut = d["cut"]
    cut_metrics = {
        "cut_frame": int(cut),
        "cut_detected_at": [int(t) for t in np.where(res["cut_flags"] > 0)[0]],
        "cut_score_at_cut_01": float(res["cut_scores"][cut]),
        "cut_rule": "score > 2.0 * median(previous within-scene scores)",
        "psnr_cut_frame_ours_db": float(
            10 * np.log10(1 / np.mean((res["out"][cut] - gt[cut]) ** 2))),
        "psnr_cut_frame_no_reset_db": float(
            10 * np.log10(1 / np.mean((res_noreset["out"][cut] - gt[cut]) ** 2))),
        "residual_energy_cut_frame_no_reset": float(
            np.mean((res_noreset["out"][cut] - gt[cut]) ** 2)),
        "residual_energy_cut_frame_ours": float(
            np.mean((res["out"][cut] - gt[cut]) ** 2)),
    }

    header = f"{'method':38s} {'static':>8s} {'motion':>8s} {'occ-entry':>10s} {'global':>8s}"
    lines = [header, "-" * len(header)]
    for r in table:
        lines.append(f"{r['label']:38s} {r['static']:8.2f} {r['motion']:8.2f} "
                     f"{r['occ']:10.2f} {r['global']:8.2f}")
    table_txt = "\n".join(lines)
    print(table_txt)
    print("\nnoise estimate:", json.dumps(noise, indent=2))
    print("\ncut metrics:", json.dumps(cut_metrics, indent=2))

    with open(os.path.join(ART, "psnr_table.txt"), "w") as f:
        f.write(table_txt + "\n")
    fidelity = {
        "motion": hf_fidelity(res["out"], gt, d["motion"]),
        "static": hf_fidelity(res["out"], gt, d["static"]),
    }
    print("\ntexture fidelity (ours):", json.dumps(fidelity, indent=2))
    with open(os.path.join(ART, "metrics.json"), "w") as f:
        json.dump({"noise": noise, "psnr": table, "cut": cut_metrics,
                   "texture_fidelity": fidelity}, f, indent=2)
    # Frames themselves are covered by the tiled PNGs and comparison.mp4;
    # keep only the scalar/map data needed to rebuild the analytic figures.
    np.savez_compressed(os.path.join(ART, "analysis_data.npz"),
             weights=res["weights"].astype(np.float16),
             n_eff=res["n_eff"], est_sigma=est.astype(np.float16),
             cut_scores=res["cut_scores"].astype(np.float16),
             cut_flags=res["cut_flags"],
             static=d["static"], motion=d["motion"], occ=d["occ"])

    visualize.psnr_curves(d, res, ART)
    visualize.noise_figure(d, est, ART)
    visualize.weight_figure(d, res, ART)
    visualize.cut_figure(d, res_noreset, res, ART)
    visualize.panel_figure(d, res, ART)
    visualize.save_tiles_and_video(d, res, ART)
    print("\nartifacts written to", os.path.abspath(ART))


if __name__ == "__main__":
    main()
