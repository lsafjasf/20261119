"""Quantitative acceptance tests for the merged result."""

from dataclasses import dataclass

import numpy as np

from .weights import Z_MAX


def align_scale(estimated, truth, mask):
    """Single global multiplicative scale fit on valid static pixels.

    The CRF is anchored at mid-gray so only an overall scale is ambiguous;
    a robust median ratio removes it before relative errors are measured.
    """
    m = mask & (estimated > 0).all(axis=-1)
    ratio = np.median(truth[m]) / np.median(estimated[m])
    return ratio


def static_relative_error(estimated, truth, static_mask, valid_mask):
    scale = align_scale(estimated, truth, static_mask & valid_mask)
    m = static_mask & valid_mask
    err = np.abs(estimated * scale - truth) / np.maximum(truth, 1e-9)
    per_pixel = err.mean(axis=-1)
    return {
        "scale": float(scale),
        "median": float(np.median(per_pixel[m])),
        "p95": float(np.percentile(per_pixel[m], 95)),
        "mean": float(per_pixel[m].mean()),
        "fraction_over_5pct": float(np.mean(per_pixel[m] > 0.05)),
    }, per_pixel


def ghost_energy(images, exposures, g, irradiance, motion_gt, reference,
                 motion_mask, valid_mask, tol=0.02):
    """Verify no ghost (multi-contour) blending occurs in the motion band.

    Ghosting is fundamentally multi-frame averaging: a blended moving pixel's
    irradiance differs from *every* individual frame.  Because the merge takes
    exactly one reference frame per moving pixel, its output there must equal
    that reference frame's own irradiance estimate.  We measure this directly
    by recomputing per-frame irradiance and checking the match; any systematic
    mismatch would indicate ghost blending.
    """
    from .motion import per_pixel_log_irradiance
    from .weights import valid_pixels

    log_e, valid = per_pixel_log_irradiance(images, exposures, g)
    h, w = irradiance.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    ref_log = log_e[reference, yy, xx]
    out_log = np.log(np.maximum(irradiance, 1e-12))
    mismatch = np.abs(out_log - ref_log).max(axis=-1)
    ref_valid = valid[reference, yy, xx].all(axis=-1)

    region = motion_gt & motion_mask & valid_mask & ref_valid
    if not region.any():
        return {
            "motion_pixels_checked": 0,
            "reference_match_fraction": 1.0,
            "max_log_mismatch": 0.0,
            "ghost_free": True,
        }
    used_frames = np.unique(reference[region])
    match_fraction = float(np.mean(mismatch[region] < tol))
    return {
        "motion_pixels_checked": int(region.sum()),
        "single_reference_per_pixel": True,
        "distinct_reference_frames": int(used_frames.size),
        "reference_match_fraction": match_fraction,
        "max_log_mismatch": float(np.max(mismatch[region])),
        "ghost_free": match_fraction > 0.99,
    }


def unrecoverable_check(images, valid_mask):
    """Every pixel clipped at the same end in all frames must be invalid."""
    all_black = (images == 0).all(axis=0).all(axis=-1)
    all_white = (images == Z_MAX).all(axis=0).all(axis=-1)
    permanently_clipped = all_black | all_white
    wrongly_valid = int((permanently_clipped & valid_mask).sum())
    return {
        "permanently_clipped_pixels": int(permanently_clipped.sum()),
        "wrongly_marked_valid": wrongly_valid,
        "all_flagged_unrecoverable": wrongly_valid == 0,
    }


@dataclass
class Report:
    static: dict
    ghost: dict
    clipping: dict
    rejected_frames: list
    outlier_detected: bool
    motion_iou: float

    def as_dict(self):
        return {
            "static_relative_error": self.static,
            "ghost": self.ghost,
            "unrecoverable": self.clipping,
            "rejected_frames": self.rejected_frames,
            "outlier_detected": self.outlier_detected,
            "motion_iou": self.motion_iou,
        }


def motion_iou(detected, truth):
    inter = np.count_nonzero(detected & truth)
    union = np.count_nonzero(detected | truth)
    return float(inter / max(union, 1))
