"""Grid straightness metric on undistorted corner observations."""

from __future__ import annotations

import cv2
import numpy as np


def _line_deviations(pts):
    """Perpendicular distances of points to their total-least-squares line."""
    centroid = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - centroid, full_matrices=False)
    direction = vt[0]
    normal = np.array([-direction[1], direction[0]])
    return np.abs((pts - centroid) @ normal)


def grid_straightness(corners, pattern, kmat, dist):
    """Measure residual line deviation of the undistorted grid (pixels)."""
    undist = cv2.undistortPoints(corners.astype(np.float64), kmat, dist,
                                 P=kmat).reshape(-1, 2)
    cols, rows = pattern
    grid = undist.reshape(rows, cols, 2)
    devs = []
    for r in range(rows):
        devs.append(_line_deviations(grid[r]))
    for c in range(cols):
        devs.append(_line_deviations(grid[:, c]))
    devs = np.concatenate(devs)
    return {"max": float(devs.max()),
            "rms": float(np.sqrt(np.mean(devs ** 2)))}
