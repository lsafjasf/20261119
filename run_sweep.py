"""Parameter sweep for the rolling-shutter experiment.

Sweeps readout duration, rotation speed and exposure (readout) direction,
and plots line-deviation RMS / valid-area metrics for distorted vs corrected
frames.  Uses frame 0 of the sequence (t0 = 0) for every configuration.
"""

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from rolling_shutter import (camera_intrinsics, effective_area,
                             line_deviation, process_frame, render_grid,
                             render_measure_grid)

W, H, GRID = 960, 720, 80
K = camera_intrinsics(W, H)
SCENE, VLINES = render_grid(W, H, GRID, 0)
MEAS = render_measure_grid(W, H, VLINES)


def evaluate(omega_deg, readout_ms, direction):
    omega = np.deg2rad(omega_deg)
    res = process_frame(SCENE, MEAS, K, omega, readout_ms / 1e3, direction, 0.0)
    rms_d, _, _ = line_deviation(res["meas_dist"], VLINES, GRID,
                                 hint_map_x=res["fwd_map"][0])
    rms_c, _, _ = line_deviation(res["meas_corr"], VLINES, GRID)
    valid_ratio, rect_ratio, _ = effective_area(~res["holes"])
    return rms_d, rms_c, valid_ratio, rect_ratio


def main():
    outdir = "rs_output/sweep"
    os.makedirs(outdir, exist_ok=True)
    base = np.array([0.0, 90.0, 30.0])

    readouts = [0, 10, 20, 30, 40]
    speeds = [0, 30, 60, 90, 120]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))

    for direction, marker in [("down", "o"), ("up", "s")]:
        rd, rc = [], []
        for r in readouts:
            a, b, _, _ = evaluate(base, r, direction)
            rd.append(a); rc.append(b)
        axes[0].plot(readouts, rd, marker + "-", color="tab:red",
                     label="distorted (%s)" % direction)
        axes[0].plot(readouts, rc, marker + "--", color="tab:blue",
                     label="corrected (%s)" % direction)
        if direction == "down":
            down_rd = list(rd)

    sd, sc, sv = [], [], []
    for s in speeds:
        a, b, v, _ = evaluate(base * (s / 90.0 if s else 0.0), 30.0, "down")
        sd.append(a); sc.append(b); sv.append(100 * v)
    axes[0].set_xlabel("readout duration (ms)")
    axes[0].set_ylabel("line deviation RMS (px)")
    axes[0].set_title("readout duration & direction")
    axes[0].legend(); axes[0].grid(alpha=0.3)

    axes[1].plot(speeds, sd, "o-", color="tab:red", label="distorted")
    axes[1].plot(speeds, sc, "s--", color="tab:blue", label="corrected")
    axes[1].set_xlabel("rotation speed (yaw deg/s, others scaled)")
    axes[1].set_ylabel("line deviation RMS (px)")
    axes[1].set_title("rotation speed (readout 30 ms, down)")
    ax2b = axes[1].twinx()
    ax2b.plot(speeds, sv, "^:", color="tab:green", label="valid %")
    ax2b.set_ylabel("valid pixel ratio (%)", color="tab:green")
    axes[1].legend(loc="upper left"); axes[1].grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "sweep.png"), dpi=110)
    print("readout sweep (down): rms_dist=%s" % np.round(down_rd, 2).tolist())
    print("speed sweep: rms_dist=%s rms_corr=%s valid%%=%s"
          % (np.round(sd, 2).tolist(), np.round(sc, 3).tolist(),
             np.round(sv, 2).tolist()))
    print("saved %s/sweep.png" % outdir)


if __name__ == "__main__":
    main()
