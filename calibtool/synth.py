"""Generate synthetic chessboard images with known camera parameters.

Images are rendered by projecting a planar chessboard with a pinhole
model plus radial/tangential distortion, so ground-truth intrinsics are
exactly known and calibration results can be verified against them.
"""

from __future__ import annotations

import json
import math
import os

import cv2
import numpy as np


def default_camera(width: int = 1280, height: int = 720) -> dict:
    """Action-camera-like ground truth: wide FOV, strong barrel distortion."""
    return {
        "width": width,
        "height": height,
        "fx": 520.0,
        "fy": 520.0,
        "cx": width / 2.0,
        "cy": height / 2.0,
        "k1": -0.32,
        "k2": 0.11,
        "p1": 0.0,
        "p2": 0.0,
        "k3": -0.015,
    }


def distort_points(pts_norm: np.ndarray, cam: dict) -> np.ndarray:
    """Apply radial+tangential distortion to normalized image points."""
    x = pts_norm[:, 0]
    y = pts_norm[:, 1]
    r2 = x * x + y * y
    r4 = r2 * r2
    r6 = r4 * r2
    radial = 1.0 + cam["k1"] * r2 + cam["k2"] * r4 + cam["k3"] * r6
    xd = x * radial + 2.0 * cam["p1"] * x * y + cam["p2"] * (r2 + 2.0 * x * x)
    yd = y * radial + cam["p1"] * (r2 + 2.0 * y * y) + 2.0 * cam["p2"] * x * y
    return np.stack([xd, yd], axis=1)


def project(board_pts, rvec, tvec, cam):
    """Project 3D board points (Z=0 plane) to distorted pixel coordinates."""
    rot, _ = cv2.Rodrigues(rvec)
    pts_cam = board_pts @ rot.T + tvec
    pts_norm = pts_cam[:, :2] / pts_cam[:, 2:3]
    pts_dist = distort_points(pts_norm, cam)
    u = cam["fx"] * pts_dist[:, 0] + cam["cx"]
    v = cam["fy"] * pts_dist[:, 1] + cam["cy"]
    return np.stack([u, v], axis=1)


def random_pose(rng, degenerate):
    """Random board pose in front of the camera.

    degenerate=True yields near-identical poses (insufficient constraint).
    """
    if degenerate:
        tilt_x = math.radians(20.0) + rng.normal(0.0, math.radians(0.3))
        tilt_y = math.radians(-8.0) + rng.normal(0.0, math.radians(0.3))
        tilt_z = rng.normal(0.0, math.radians(0.3))
        tvec = np.array([rng.normal(0.0, 0.002),
                         rng.normal(-0.02, 0.002),
                         rng.normal(0.9, 0.004)])
    else:
        tilt_x = rng.uniform(math.radians(-35), math.radians(35))
        tilt_y = rng.uniform(math.radians(-35), math.radians(35))
        tilt_z = rng.uniform(math.radians(-15), math.radians(15))
        tvec = np.array([rng.uniform(-0.28, 0.28),
                         rng.uniform(-0.18, 0.18),
                         rng.uniform(0.5, 0.95)])
    return np.array([tilt_x, tilt_y, tilt_z]), tvec


def render_board(cam, rvec, tvec, pattern, square, rng):
    """Render a chessboard image by filling projected square polygons."""
    width, height = cam["width"], cam["height"]
    img = np.full((height, width), 200, dtype=np.uint8)
    cols, rows = pattern[0] + 1, pattern[1] + 1  # full square counts
    for row in range(rows):
        for col in range(cols):
            quad = np.array([
                [col * square, row * square, 0.0],
                [(col + 1) * square, row * square, 0.0],
                [(col + 1) * square, (row + 1) * square, 0.0],
                [col * square, (row + 1) * square, 0.0],
            ])
            px = project(quad, rvec, tvec, cam)
            if not np.all(np.isfinite(px)):
                continue
            color = 30 if (row + col) % 2 == 0 else 235
            cv2.fillConvexPoly(img, px.astype(np.int32), int(color))
    noise = rng.normal(0.0, 1.5, img.shape)
    return np.clip(img.astype(np.float64) + noise, 0, 255).astype(np.uint8)


def generate_dataset(out_dir: str, count: int = 12, seed: int = 0,
                     degenerate: bool = False, pattern=(9, 6),
                     square: float = 0.025) -> dict:
    """Render `count` chessboard images plus a ground-truth JSON file."""
    os.makedirs(out_dir, exist_ok=True)
    rng = np.random.default_rng(seed)
    cam = default_camera()
    poses = []
    for idx in range(count):
        rvec, tvec = random_pose(rng, degenerate)
        img = render_board(cam, rvec, tvec, pattern, square, rng)
        name = f"board_{idx:02d}.png"
        cv2.imwrite(os.path.join(out_dir, name), img)
        poses.append({"image": name, "rvec": rvec.tolist(),
                      "tvec": tvec.tolist()})
    truth = {
        "camera": cam,
        "pattern": list(pattern),
        "square_size": square,
        "degenerate": degenerate,
        "poses": poses,
    }
    with open(os.path.join(out_dir, "ground_truth.json"), "w") as fh:
        json.dump(truth, fh, indent=2)
    return truth
