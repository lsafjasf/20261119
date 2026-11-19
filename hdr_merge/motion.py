"""Motion region detection and reference-frame selection (ghost control)."""

import numpy as np
import cv2
import warnings

from .weights import Z_MAX, hat_weights, valid_pixels


def _weighted_spread(log_e, weights, min_frames):
    """Cross-frame dispersion from well-exposed observations only.

    Uses the largest deviation from the per-pixel cross-frame median: a moving
    object occupies each pixel in only a frame or two, so MAD/percentile
    statistics would treat those frames as outliers and discard them.
    """
    n_frames, h, w, _ = log_e.shape
    good = weights > (Z_MAX / 25.0)
    count = good.sum(axis=0)
    usable = (count >= min_frames).all(axis=-1)
    masked = np.where(good, log_e, np.nan)
    with np.errstate(invalid="ignore", all="ignore"), \
            warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        med = np.nanmedian(masked, axis=0)
        dev = np.abs(masked - med[None])
        spread = np.nanmax(dev, axis=0).max(axis=-1)
    spread[~usable] = 0.0
    return spread, usable


def per_pixel_log_irradiance(images, exposures, g):
    """ln(E) estimate of every observation; NaN where clipped."""
    images = np.asarray(images)
    n_frames, h, w, _ = images.shape
    log_t = np.log(np.asarray(exposures, dtype=np.float64))
    log_e = np.empty((n_frames, h, w, 3))
    for j in range(n_frames):
        for c in range(3):
            log_e[j, :, :, c] = g[images[j, :, :, c], c] - log_t[j]
    valid = valid_pixels(images)
    log_e[~valid] = np.nan
    return log_e, valid


def detect_motion(images, exposures, g, k=4.0, min_frames=2, dilate=4):
    """Pixels whose cross-frame irradiance estimates disagree are moving.

    A robust scale (median absolute deviation) is learned from static pixels
    first, so the threshold adapts to the actual noise level of the sequence.
    """
    log_e, valid = per_pixel_log_irradiance(images, exposures, g)
    weights = hat_weights(images) * valid
    spread, usable = _weighted_spread(log_e, weights, min_frames)

    ref = spread[spread > 0]
    if ref.size == 0:
        return np.zeros(images.shape[1:3], dtype=bool)
    med = np.median(ref)
    mad = np.median(np.abs(ref - med)) + 1e-9
    threshold = med + k * 1.4826 * mad
    motion = ((spread > threshold) & usable).astype(np.uint8)
    if dilate:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * dilate + 1, 2 * dilate + 1)
        )
        # Closing fills interior holes; a small opening removes isolated
        # speckle detections before the mask is grown to cover object edges.
        motion = cv2.morphologyEx(motion, cv2.MORPH_CLOSE, kernel, iterations=2)
        motion = cv2.morphologyEx(motion, cv2.MORPH_OPEN, kernel, iterations=1)
        motion = cv2.dilate(motion, kernel, iterations=1)
    motion = _keep_large_components(motion.astype(bool))
    return motion


def _keep_large_components(mask, min_area=40):
    """Drop tiny connected components that are noise rather than the object."""
    num, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8
    )
    keep = np.zeros_like(mask)
    for label in range(1, num):
        if stats[label, cv2.CC_STAT_AREA] >= min_area:
            keep |= labels == label
    return keep


def choose_reference_frames(images, valid, motion_mask):
    """Per-pixel best single frame inside a motion region.

    The frame whose value is closest to mid-gray wins: it has the highest
    saturation weight and is far from either clipping end.  A single frame is
    selected per pixel so multiple contours (ghosts) can never be blended in.
    """
    n_frames, h, w, _ = images.shape
    mid = (Z_MAX + 1) / 2.0
    distance = np.abs(images.astype(np.float64) - mid).mean(axis=-1)
    distance[~valid.all(axis=-1)] = np.inf
    ref = np.argmin(distance, axis=0)
    fallback = np.zeros((h, w), dtype=np.int64)
    return np.where(motion_mask, ref, fallback)
