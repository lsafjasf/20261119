"""Saturation / hat weighting used for both CRF estimation and merging."""

import numpy as np

Z_MAX = 255
Z_MIN = 0


def hat_weights(z):
    """Triangular (tent) weight: maximal at mid-gray, zero at both clipping ends."""
    z = np.asarray(z, dtype=np.float64)
    mid = (Z_MAX + Z_MIN) / 2.0
    w = mid - np.abs(z - mid)
    return np.maximum(w, 1e-3)


def valid_pixels(images):
    """Boolean mask of frames/pixels that are not clipped at either end."""
    return (images > Z_MIN) & (images < Z_MAX)
