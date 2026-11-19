"""Calibration core: fit intrinsics, per-view errors, degeneracy checks."""

from __future__ import annotations

import math

import cv2
import numpy as np

# Pose-diversity thresholds below which the constraint set is considered
# too weak for a reliable calibration.
MIN_VIEWS = 4
MIN_NORMAL_SPREAD_DEG = 10.0
MIN_TRANSLATION_SPREAD = 0.05  # std of camera-board distance ratio


def fit(obj_pts, img_pts_list, image_size, rational=False):
    """Run OpenCV calibration; returns a result dict."""
    obj = [obj_pts.astype(np.float32) for _ in img_pts_list]
    img = [c.astype(np.float32) for c in img_pts_list]
    flags = cv2.CALIB_RATIONAL_MODEL if rational else 0
    rms, kmat, dist, rvecs, tvecs = cv2.calibrateCamera(
        obj, img, image_size, None, None,
        flags=flags,
        criteria=(cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
                  60, 1e-7))
    per_view = []
    for i, (o, c) in enumerate(zip(obj, img)):
        proj, _ = cv2.projectPoints(o, rvecs[i], tvecs[i], kmat, dist)
        err = np.linalg.norm(proj.reshape(-1, 2) - c.reshape(-1, 2), axis=1)
        per_view.append({"rms": float(np.sqrt(np.mean(err ** 2))),
                         "max": float(err.max())})
    return {"kmat": kmat, "dist": dist.ravel(), "rms": float(rms),
            "rvecs": rvecs, "tvecs": tvecs, "per_view": per_view}


def pose_diversity(rvecs, tvecs):
    """Measure pose diversity: (normal_spread_deg, translation_spread)."""
    normals = []
    dists = []
    for rvec, tvec in zip(rvecs, tvecs):
        rot, _ = cv2.Rodrigues(rvec)
        normals.append(rot @ np.array([0.0, 0.0, 1.0]))
        dists.append(float(np.linalg.norm(tvec)))
    normals = np.array(normals)
    dists = np.array(dists)
    mean_n = normals.mean(axis=0)
    mean_n /= np.linalg.norm(mean_n)
    cosang = np.clip(normals @ mean_n, -1.0, 1.0)
    spread_deg = float(np.degrees(np.arccos(cosang)).mean())
    t_spread = float(dists.std() / max(dists.mean(), 1e-9))
    return spread_deg, t_spread


def degeneracy_report(n_views, rvecs, tvecs):
    """Decide whether the observations constrain the calibration enough."""
    problems = []
    spread_deg, t_spread = (0.0, 0.0)
    if n_views < MIN_VIEWS:
        problems.append(
            f"only {n_views} usable views (need >= {MIN_VIEWS})")
    if n_views >= 2:
        spread_deg, t_spread = pose_diversity(rvecs, tvecs)
        if spread_deg < MIN_NORMAL_SPREAD_DEG:
            problems.append(
                f"board normal spread {spread_deg:.2f} deg "
                f"< {MIN_NORMAL_SPREAD_DEG} deg: poses are nearly "
                "identical, focal length and distortion are not "
                "independently observable")
        if t_spread < MIN_TRANSLATION_SPREAD:
            problems.append(
                f"board distance variation {t_spread:.3f} "
                f"< {MIN_TRANSLATION_SPREAD}: all views at nearly the "
                "same depth, scale/ambiguity is poorly constrained")
    return {"degenerate": bool(problems), "problems": problems,
            "normal_spread_deg": spread_deg, "translation_spread": t_spread}
