"""Chessboard corner detection over a directory of images."""

from __future__ import annotations

import os

import cv2
import numpy as np

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")


def list_images(img_dir):
    names = [n for n in sorted(os.listdir(img_dir))
             if n.lower().endswith(IMAGE_EXTS)]
    return [os.path.join(img_dir, n) for n in names]


def object_points(pattern, square):
    """3D coordinates of inner corners on the Z=0 board plane."""
    cols, rows = pattern
    grid = np.zeros((rows * cols, 3), dtype=np.float64)
    grid[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * square
    return grid


def detect_corners(img_paths, pattern):
    """Detect sub-pixel chessboard corners in every image."""
    results = []
    for path in img_paths:
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            results.append({"path": path, "ok": False, "corners": None,
                            "shape": None, "reason": "unreadable"})
            continue
        flags = (cv2.CALIB_CB_ADAPTIVE_THRESH
                 | cv2.CALIB_CB_NORMALIZE_IMAGE
                 | cv2.CALIB_CB_FAST_CHECK)
        ok, corners = cv2.findChessboardCorners(img, pattern, flags)
        if ok:
            criteria = (cv2.TERM_CRITERIA_EPS
                        + cv2.TERM_CRITERIA_MAX_ITER, 40, 1e-3)
            corners = cv2.cornerSubPix(img, corners, (5, 5), (-1, -1),
                                       criteria)
        results.append({"path": path, "ok": bool(ok),
                        "corners": corners if ok else None,
                        "shape": img.shape,
                        "reason": None if ok else "pattern not found"})
    return results
