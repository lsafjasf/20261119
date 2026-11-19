"""Detect and reject outlier exposures (flicker / wrong exposure metadata)."""

from dataclasses import dataclass

import numpy as np
import warnings

from .weights import Z_MAX, hat_weights, valid_pixels


@dataclass
class OutlierResult:
    kept: np.ndarray          # indices of frames retained
    rejected: np.ndarray      # indices rejected
    scores: np.ndarray        # per-frame robust inconsistency score (log units)
    threshold: float


def frame_consistency_scores(images, exposures, g):
    """Robust per-frame deviation of per-pixel log irradiance estimates.

    For every non-clipped observation we form ln(E) = g(z) - ln(t) and compare
    it against the cross-frame median for that pixel.  A frame whose whole
    illumination is inconsistent (flicker, wrong exposure time) stands out.
    """
    images = np.asarray(images)
    n_frames, h, w, _ = images.shape
    log_t = np.log(np.asarray(exposures, dtype=np.float64))
    valid = valid_pixels(images)

    log_e = np.full((n_frames, h, w, 3), np.nan)
    for j in range(n_frames):
        for c in range(3):
            log_e[j, :, :, c] = g[images[j, :, :, c], c] - log_t[j]
    log_e[~valid] = np.nan
    weights = hat_weights(images) * valid

    # Reference per pixel from all *other* frames so a corrupted frame cannot
    # contaminate its own reference or its neighbours'.
    residual = np.full_like(log_e, np.nan)
    for j in range(n_frames):
        others = np.delete(log_e, j, axis=0)
        with np.errstate(invalid="ignore", all="ignore"), \
                warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            ref = np.nanmedian(others, axis=0)
        shift = np.nanmedian(
            np.where(valid[j], log_e[j] - ref, np.nan)
        )
        diff = np.abs(log_e[j] - ref - shift)
        residual[j] = np.where(valid[j], diff, np.nan)

    # Robust per-frame score.  We deliberately do NOT exclude moving pixels:
    # the moving disk is small and is suppressed by the spatial median, while a
    # large local flare affects many pixels and therefore moves the median.
    # Dark frames with too few well-exposed pixels fall back to every usable
    # pixel so the score stays finite.
    w = weights
    score = np.empty(n_frames)
    finite = np.isfinite(residual).all(axis=-1)
    for j in range(n_frames):
        d = np.nan_to_num(residual[j], nan=0.0).max(axis=-1)
        ww = w[j].max(axis=-1)
        good = (ww > Z_MAX / 4.0) & finite[j]
        if good.sum() < 30:
            good = (ww > 0) & finite[j]
        score[j] = np.median(d[good]) if good.any() else 0.0
    return score


def reject_outliers(images, exposures, g, k=2.0, min_keep=3):
    """Reject frames whose robust deviation exceeds a log-space tolerance."""
    scores = frame_consistency_scores(images, exposures, g)
    finite = scores[np.isfinite(scores)]
    med = np.median(finite)
    mad = np.median(np.abs(finite - med)) + 1e-9
    threshold = med + k * 1.4826 * mad
    rejected = np.flatnonzero(scores > threshold)
    kept = np.flatnonzero(scores <= threshold)
    if kept.size < min_keep:
        order = np.argsort(scores)
        kept = np.sort(order[:max(min_keep, kept.size)])
        rejected = np.setdiff1d(np.arange(len(scores)), kept)
    return OutlierResult(kept=kept, rejected=rejected,
                         scores=scores, threshold=threshold)
