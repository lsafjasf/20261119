"""Saturation-weighted merge to linear irradiance with a validity mask."""

from dataclasses import dataclass

import numpy as np

from .motion import choose_reference_frames, detect_motion
from .outlier import reject_outliers
from .response import estimate_response
from .weights import Z_MAX, hat_weights, valid_pixels


@dataclass
class MergeResult:
    irradiance: np.ndarray      # (H, W, 3) float64 linear irradiance
    valid: np.ndarray           # (H, W) bool; False = unrecoverable
    motion_mask: np.ndarray     # (H, W) bool detected moving pixels
    reference: np.ndarray       # (H, W) int frame used inside motion regions
    rejected: np.ndarray        # frame indices rejected as outliers
    g: np.ndarray               # (256, 3) estimated inverse response


def merge_exposures(images, exposures, g=None, reject=True,
                    motion_kwargs=None):
    """End-to-end pipeline.

    1. estimate the response curve (if not supplied),
    2. reject outlier exposures,
    3. detect moving pixels and pick one reference frame per moving pixel,
    4. merge static pixels with saturation weights in log space,
    5. mark permanently clipped pixels as unrecoverable.
    """
    images = np.asarray(images)
    exposures = np.asarray(exposures, dtype=np.float64)
    if g is None:
        g = estimate_response(images, exposures, n_samples=1500, lam=40.0)

    rejected = np.array([], dtype=np.int64)
    if reject:
        result = reject_outliers(images, exposures, g)
        rejected = result.rejected
        kept = result.kept
        images = images[kept]
        exposures = exposures[kept]

    motion_kwargs = motion_kwargs or {}
    preliminary_motion = detect_motion(images, exposures, g, **motion_kwargs)
    # Re-estimate the curve on clean, static pixels only so neither the
    # rejected flicker frame nor the moving object bias the response.
    g = estimate_response(
        images, exposures, n_samples=1500, lam=40.0,
        static_mask=~preliminary_motion,
    )
    motion_mask = detect_motion(images, exposures, g, **motion_kwargs)

    n_frames, h, w, _ = images.shape
    log_t = np.log(exposures)
    weights = hat_weights(images)
    valid = valid_pixels(images)
    weights = weights * valid
    reference = choose_reference_frames(images, valid, motion_mask)

    log_e = np.empty((n_frames, h, w, 3))
    for j in range(n_frames):
        for c in range(3):
            log_e[j, :, :, c] = g[images[j, :, :, c], c] - log_t[j]

    # Static pixels: weighted average over all usable frames.
    w_sum = weights.sum(axis=0)
    static_ok = (w_sum > 0).all(axis=-1) & (~motion_mask)
    weighted_log = np.nansum(
        np.where(valid, log_e, 0.0) * weights, axis=0
    ) / np.maximum(w_sum, 1e-12)
    irradiance = np.exp(weighted_log)

    # Motion pixels: take exactly one reference frame (no ghost blend).
    yy, xx = np.mgrid[0:h, 0:w]
    ref_valid_all = valid[reference, yy, xx].all(axis=-1)
    for c in range(3):
        ref_values = log_e[reference, yy, xx, c]
        ref_valid = valid[reference, yy, xx, c]
        irradiance[:, :, c] = np.where(
            motion_mask & ref_valid,
            np.exp(ref_values),
            irradiance[:, :, c],
        )

    any_usable = valid.any(axis=0).all(axis=-1)
    static_ok = static_ok | (motion_mask & ref_valid_all)
    valid_mask = any_usable & static_ok

    # A pixel clipped at the same end in *every* frame cannot be recovered.
    all_black = (images == 0).all(axis=0).all(axis=-1)
    all_white = (images == Z_MAX).all(axis=0).all(axis=-1)
    valid_mask &= ~(all_black | all_white)
    irradiance[~valid_mask, :] = 0.0

    return MergeResult(
        irradiance=irradiance,
        valid=valid_mask,
        motion_mask=motion_mask,
        reference=reference,
        rejected=rejected,
        g=g,
    )
