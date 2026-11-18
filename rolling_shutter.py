"""Rolling-shutter distortion & correction experiment.

Pipeline:
1. Render a known calibration grid (the scene).
2. Forward pass: simulate a rolling-shutter camera whose orientation changes
   during the frame exposure. Every sensor row y is exposed at time t(y);
   the camera rotation at that instant is R(t(y)). Row y of the distorted
   frame is the scene warped by the row homography H(y) = K R(t(y)) K^-1.
3. Correction: for every output row y, resample the distorted frame with the
   same row's inverse homography. This is a genuine per-row warp, NOT a
   single whole-frame affine transform.
4. Metrics: straight-line deviation before/after correction, effective frame
   area, and the unrecoverable hole mask.

Zero rotation (wx = wy = wz = 0) must reproduce the input bit-exactly.
"""

import argparse
import os

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def camera_intrinsics(width, height, focal=None):
    """Pinhole intrinsics; principal point at the image centre."""
    f = focal if focal is not None else 0.9 * max(width, height)
    return np.array([[f, 0.0, (width - 1) / 2.0],
                     [0.0, f, (height - 1) / 2.0],
                     [0.0, 0.0, 1.0]], dtype=np.float64)


def rodrigues(rotvec):
    """Rotation matrix from a rotation vector (axis * angle, radians)."""
    theta = np.linalg.norm(rotvec)
    if theta < 1e-15:
        return np.eye(3)
    k = rotvec / theta
    kx = np.array([[0.0, -k[2], k[1]],
                   [k[2], 0.0, -k[0]],
                   [-k[1], k[0], 0.0]])
    return np.eye(3) + np.sin(theta) * kx + (1.0 - np.cos(theta)) * (kx @ kx)


def row_exposure_times(height, readout_s, direction, t0=0.0):
    """Exposure timestamp of every sensor row.

    direction='down': row 0 is exposed first (top-to-bottom readout).
    direction='up'  : the last row is exposed first (bottom-to-top readout).
    """
    frac = np.arange(height, dtype=np.float64) / max(height - 1, 1)
    if direction == "up":
        frac = frac[::-1]
    return t0 + frac * readout_s


def row_rotations(times, omega):
    """R(t) = exp([omega * t]_x) for every row timestamp."""
    return np.stack([rodrigues(omega * t) for t in times])


# ---------------------------------------------------------------------------
# Scene rendering
# ---------------------------------------------------------------------------

def render_grid(width, height, grid_px, frame_idx=0):
    """Known calibration scene: grid + border + diagonals + frame marker.

    Returns the image (uint8 gray) and the ideal x-positions of the vertical
    grid lines (used later for the straight-line deviation metric).
    """
    img = np.full((height, width), 255, np.uint8)
    vlines = list(range(grid_px, width, grid_px))
    hlines = list(range(grid_px, height, grid_px))
    for x in vlines:
        cv2.line(img, (x, 0), (x, height - 1), 0, 2)
    for y in hlines:
        cv2.line(img, (0, y), (width - 1, y), 0, 2)
    cv2.rectangle(img, (0, 0), (width - 1, height - 1), 0, 3)
    cv2.line(img, (0, 0), (width - 1, height - 1), 120, 1)
    cv2.line(img, (0, height - 1), (width - 1, 0), 120, 1)
    cv2.circle(img, (width // 2, height // 2), min(width, height) // 4, 0, 2)
    cv2.putText(img, "F%02d" % frame_idx, (12, 34),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, 0, 2)
    return img, vlines


def render_measure_grid(width, height, vlines):
    """Clean vertical-line grid used ONLY for the deviation measurement;
    it is warped with exactly the same per-row maps as the scene."""
    img = np.full((height, width), 255, np.uint8)
    for x in vlines:
        cv2.line(img, (x, 0), (x, height - 1), 0, 2)
    return img


# ---------------------------------------------------------------------------
# Per-row warp fields (forward distortion & inverse correction)
# ---------------------------------------------------------------------------

def build_row_maps(K, rotations, width, height, invert=False):
    """Per-row remap coordinates for cv2.remap.

    Row y is warped with its OWN homography H(y) = K R(y) K^-1 (or its
    inverse when invert=True).  The maps are assembled row by row; they are
    explicitly NOT a single whole-frame affine transform.
    """
    kinv = np.linalg.inv(K)
    map_x = np.empty((height, width), np.float32)
    map_y = np.empty((height, width), np.float32)
    xs = np.arange(width, dtype=np.float64)
    ones = np.ones(width, dtype=np.float64)
    for y in range(height):
        h = K @ rotations[y] @ kinv
        if invert:
            h = np.linalg.inv(h)
        pts = np.stack([xs, np.full(width, float(y)), ones])
        dst = h @ pts
        dst /= dst[2:3]
        map_x[y] = dst[0]
        map_y[y] = dst[1]
    return map_x, map_y


def assert_per_row(map_x, map_y, omega):
    """Guard: with non-zero rotation the warp must vary from row to row."""
    if np.linalg.norm(omega) < 1e-12:
        return
    same = np.allclose(map_x, map_x[0:1, :]) and np.allclose(map_y, map_y[0:1, :])
    if same:
        raise AssertionError("warp field is row-constant: this would be a "
                             "whole-frame affine transform, not per-row correction")


def warp_and_validity(image, map_x, map_y):
    """cv2.remap plus a mask of pixels sampled from inside the source frame."""
    h, w = image.shape[:2]
    out = cv2.remap(image, map_x, map_y, cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    valid = ((map_x >= 0) & (map_x <= w - 1) &
             (map_y >= 0) & (map_y <= h - 1))
    return out, valid


# ---------------------------------------------------------------------------
# One frame of the experiment
# ---------------------------------------------------------------------------

def process_frame(scene, meas, K, omega, readout_s, direction, t0):
    """Distort one frame with the rolling-shutter model, then correct it."""
    h, w = scene.shape[:2]
    times = row_exposure_times(h, readout_s, direction, t0)
    rotations = row_rotations(times, omega)

    # Forward model: distorted(u) = scene(H(y)^-1 u), row-wise.
    fwd_x, fwd_y = build_row_maps(K, rotations, w, h, invert=True)
    # Correction: corrected(u) = distorted(H(y) u), row-wise inverse mapping.
    cor_x, cor_y = build_row_maps(K, rotations, w, h, invert=False)
    assert_per_row(cor_x, cor_y, omega)

    distorted, _ = warp_and_validity(scene, fwd_x, fwd_y)
    corrected, valid = warp_and_validity(distorted, cor_x, cor_y)
    holes = ~valid
    # The measurement grid goes through the identical warp chain.
    meas_dist, _ = warp_and_validity(meas, fwd_x, fwd_y)
    meas_corr, _ = warp_and_validity(meas_dist, cor_x, cor_y)
    return {"distorted": distorted, "corrected": corrected,
            "meas_dist": meas_dist, "meas_corr": meas_corr,
            "holes": holes, "times": times,
            "fwd_map": (fwd_x, fwd_y), "cor_map": (cor_x, cor_y)}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def line_deviation(img, vlines, spacing, thresh=128, hint_map_x=None):
    """Straight-line deviation of vertical grid lines, measured on the image.

    Each line is tracked top-to-bottom: a window of +/-0.4*spacing around the
    current track position is centroided per row, and the track recentres on
    the measurement.  hint_map_x is the forward-remap x field (dst -> src);
    the expected output position of line c in row y is obtained by inverting
    that 1-D correspondence, and only initialises/recentres the track.
    The centroid uses darkness weights (thresh - pixel) for sub-pixel
    sensitivity instead of a hard binary mask.
    Returns (rms, max_abs, mean_deviation_per_row).
    """
    h, w = img.shape[:2]
    wgt = np.clip(thresh - img.astype(np.float64), 0.0, None)
    half = max(8, int(0.4 * spacing))
    xs_all = np.arange(w, dtype=np.float64)
    dev_matrix = []
    for c in vlines:
        if hint_map_x is not None:
            # expected distorted position of source line c in every row
            hint = np.array([
                np.interp(c, hint_map_x[y], xs_all) for y in range(h)])
            if not np.all(np.diff(hint_map_x, axis=1) > 0):
                hint = None  # non-monotonic row: fall back to ideal
        else:
            hint = None
        track = float(hint[0]) if hint is not None else float(c)
        dev = np.full(h, np.nan)
        for y in range(h):
            x0 = int(round(track)) - half
            x1 = int(round(track)) + half + 1
            if x1 < 0 or x0 >= w:
                continue
            x0c, x1c = max(0, x0), min(w, x1)
            row = wgt[y, x0c:x1c]
            n = float(row.sum())
            if n <= 0:
                continue
            centroid = float((xs_all[x0c:x1c] * row).sum() / n)
            dev[y] = centroid - c
            track = centroid
            if hint is not None and abs(centroid - hint[y]) > half:
                track = float(hint[y])  # lost track: re-centre from hint
        dev_matrix.append(dev)
    dm = np.stack(dev_matrix)
    with np.errstate(invalid="ignore"):
        curve = np.nanmean(dm, axis=0)
    alldev = dm[~np.isnan(dm)]
    if alldev.size == 0:
        return 0.0, 0.0, np.zeros(h)
    rms = float(np.sqrt(np.mean(alldev ** 2)))
    maxabs = float(np.max(np.abs(alldev)))
    return rms, maxabs, np.nan_to_num(curve, nan=0.0)


def effective_area(valid):
    """Effective frame area from the validity mask.

    Returns (valid_pixel_ratio, max_rect_ratio, rect) where rect is the
    largest all-valid axis-aligned rectangle (maximal-rectangle algorithm).
    """
    h, w = valid.shape
    ratio = float(valid.mean())
    heights = np.zeros(w, dtype=np.int64)
    best_area, best_rect = 0, (0, 0, -1, -1)
    for y in range(h):
        heights = np.where(valid[y], heights + 1, 0)
        stack = []
        for x in range(w + 1):
            cur = heights[x] if x < w else 0
            start = x
            while stack and stack[-1][1] > cur:
                sx, sh = stack.pop()
                area = sh * (x - sx)
                if area > best_area:
                    best_area = area
                    best_rect = (sx, y - sh + 1, x - 1, y)
                start = sx
            stack.append((start, cur))
    return ratio, best_area / float(h * w), best_rect


# ---------------------------------------------------------------------------
# Visualisation
# ---------------------------------------------------------------------------

def hole_overlay(corrected, holes, rect=None):
    rgb = cv2.cvtColor(corrected, cv2.COLOR_GRAY2RGB)
    rgb[holes] = (255, 0, 0)
    if rect is not None:
        x0, y0, x1, y1 = rect
        cv2.rectangle(rgb, (x0, y0), (x1, y1), (0, 200, 0), 2)
    return rgb


def save_frame_figure(path, scene, result, vlines, spacing, metrics, title):
    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    panels = [(scene, "scene (ground truth)"),
              (result["distorted"], "distorted (rolling shutter)"),
              (result["corrected"], "corrected (per-row inverse map)")]
    for ax, (img, name) in zip(axes[0], panels):
        ax.imshow(img, cmap="gray", vmin=0, vmax=255)
        ax.set_title(name)
        ax.axis("off")
    _, _, rect = effective_area(~result["holes"])
    axes[1, 0].imshow(hole_overlay(result["corrected"], result["holes"], rect))
    axes[1, 0].set_title("holes (red), largest valid crop (green)")
    axes[1, 0].axis("off")
    rms_d, _, curve_d = line_deviation(result["meas_dist"], vlines, spacing,
                                       hint_map_x=result["fwd_map"][0])
    rms_c, _, curve_c = line_deviation(result["meas_corr"], vlines, spacing)
    axes[1, 1].plot(curve_d, label="distorted (rms=%.2f px)" % rms_d)
    axes[1, 1].plot(curve_c, label="corrected (rms=%.2f px)" % rms_c)
    axes[1, 1].set_title("vertical-line deviation per row")
    axes[1, 1].set_xlabel("row y")
    axes[1, 1].set_ylabel("dx (px)")
    axes[1, 1].legend()
    axes[1, 1].grid(alpha=0.3)
    axes[1, 2].axis("off")
    axes[1, 2].text(0.0, 0.9, metrics, va="top", family="monospace",
                    fontsize=10)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def save_summary(path, table):
    frames = [r["frame"] for r in table]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    axes[0].plot(frames, [r["rms_dist"] for r in table], "o-", label="distorted")
    axes[0].plot(frames, [r["rms_corr"] for r in table], "s-", label="corrected")
    axes[0].set_title("line deviation RMS (px)")
    axes[0].set_xlabel("frame")
    axes[0].legend()
    axes[0].grid(alpha=0.3)
    axes[1].plot(frames, [r["valid_ratio"] for r in table], "o-")
    axes[1].set_title("valid pixel ratio after correction")
    axes[1].set_xlabel("frame")
    axes[1].grid(alpha=0.3)
    axes[2].plot(frames, [r["rect_ratio"] for r in table], "o-")
    axes[2].set_title("largest fully-valid crop ratio")
    axes[2].set_xlabel("frame")
    axes[2].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Rolling-shutter correction experiment")
    p.add_argument("--width", type=int, default=960)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--frames", type=int, default=5, help="sequence length")
    p.add_argument("--readout-ms", type=float, default=30.0,
                   help="full-frame readout duration in ms")
    p.add_argument("--direction", choices=["down", "up"], default="down",
                   help="readout (exposure scan) direction")
    p.add_argument("--wx", type=float, default=0.0, help="pitch rate, deg/s")
    p.add_argument("--wy", type=float, default=90.0, help="yaw rate, deg/s")
    p.add_argument("--wz", type=float, default=30.0, help="roll rate, deg/s")
    p.add_argument("--grid-px", type=int, default=80, help="grid spacing")
    p.add_argument("--outdir", default="rs_output")
    return p.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    K = camera_intrinsics(args.width, args.height)
    omega = np.deg2rad([args.wx, args.wy, args.wz])
    readout_s = args.readout_ms / 1e3
    os.makedirs(args.outdir, exist_ok=True)
    zero_rot = np.linalg.norm(omega) < 1e-12

    print("K =\n%s" % K)
    print("readout %.1f ms, direction %s, omega=(%.1f, %.1f, %.1f) deg/s"
          % (args.readout_ms, args.direction, args.wx, args.wy, args.wz))
    header = ("frame  rms_dist  rms_corr  max_dist  max_corr  "
              "valid%  crop%  hole%")
    print(header)
    table = []
    for fi in range(args.frames):
        scene, vlines = render_grid(args.width, args.height, args.grid_px, fi)
        meas = render_measure_grid(args.width, args.height, vlines)
        t0 = fi / 30.0  # 30 fps sequence: each frame starts later
        res = process_frame(scene, meas, K, omega, readout_s, args.direction, t0)

        rms_d, max_d, _ = line_deviation(res["meas_dist"], vlines, args.grid_px,
                                         hint_map_x=res["fwd_map"][0])
        rms_c, max_c, _ = line_deviation(res["meas_corr"], vlines, args.grid_px)
        valid_ratio, rect_ratio, _ = effective_area(~res["holes"])
        hole_ratio = float(res["holes"].mean())
        row = {"frame": fi, "rms_dist": rms_d, "rms_corr": rms_c,
               "valid_ratio": valid_ratio, "rect_ratio": rect_ratio}
        table.append(row)
        print("%5d  %8.3f  %8.3f  %8.2f  %8.2f  %6.2f  %6.2f  %6.3f"
              % (fi, rms_d, rms_c, max_d, max_c,
                 100 * valid_ratio, 100 * rect_ratio, 100 * hole_ratio))

        if zero_rot:
            # Zero rotation must not introduce ANY extra geometric change.
            assert np.array_equal(res["distorted"], scene), "distorted != scene"
            assert np.array_equal(res["corrected"], scene), "corrected != scene"
            assert hole_ratio == 0.0, "unexpected holes at zero rotation"

        metrics_txt = (
            "line dev RMS : %.3f -> %.3f px\n"
            "line dev max : %.2f -> %.2f px\n"
            "valid pixels : %.2f %%\n"
            "usable crop  : %.2f %%\n"
            "holes        : %.3f %%"
            % (rms_d, rms_c, max_d, max_c,
               100 * valid_ratio, 100 * rect_ratio, 100 * hole_ratio))
        save_frame_figure(
            os.path.join(args.outdir, "frame_%02d.png" % fi),
            scene, res, vlines, args.grid_px, metrics_txt,
            "frame %d | readout %.0f ms %s | omega=(%.1f,%.1f,%.1f) deg/s"
            % (fi, args.readout_ms, args.direction,
               args.wx, args.wy, args.wz))

    save_summary(os.path.join(args.outdir, "summary.png"), table)
    if zero_rot:
        print("ZERO-ROTATION CHECK PASSED: output is bit-exact, no holes.")
    print("figures written to %s/" % args.outdir)
