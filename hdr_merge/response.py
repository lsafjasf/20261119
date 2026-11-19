"""Camera response function estimation (Debevec & Malik 1997).

Solves the weighted least-squares system

    w(z_ij) * (g(z_ij) - ln(E_i) - ln(t_j)) = 0

with a smoothness regulariser on g, anchored at g(128) = 0.
"""

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import lsqr

from .weights import Z_MAX, hat_weights


def _sample_positions(images, n_samples, rng, static_mask=None):
    """Stratified sampling over observed intensities.

    Pure spatial random sampling leaves many 8-bit intensity levels with no
    observation, so the corresponding g(z) entries are unconstrained and the
    smoothness prior alone cannot anchor them.  We instead pick pixels spread
    across the full intensity histogram of a middle frame.
    """
    n_frames, h, w, _ = images.shape
    mid = images[n_frames // 2].mean(axis=-1)
    flat = mid.ravel()
    if static_mask is not None:
        flat = np.where(static_mask.ravel(), flat, np.inf)
    order = np.argsort(flat, kind="stable")
    if static_mask is not None:
        order = order[np.isfinite(flat[order])]
    if n_samples >= order.size:
        idx = order
    else:
        pick = np.linspace(0, order.size - 1, n_samples).astype(np.int64)
        idx = order[pick]
    rng.shuffle(idx)
    return np.unravel_index(idx, (h, w))


def _build_and_solve(z_samples, log_t, lam, weights):
    """Solve for one channel. z_samples: (N_pixels, N_frames) uint8."""
    n_pix, n_frames = z_samples.shape
    rows, cols, data, rhs = [], [], [], []
    row = 0
    w_table = hat_weights(np.arange(256))

    def add(r, c, v):
        rows.append(r)
        cols.append(c)
        data.append(v)

    for i in range(n_pix):
        for j in range(n_frames):
            z = int(z_samples[i, j])
            wij = weights[i, j]
            if wij <= 0:
                continue
            add(row, z, wij)
            add(row, 256 + i, -wij)
            rhs.append(wij * log_t[j])
            row += 1

    for z in range(1, 255):
        wz = w_table[z + 1]
        add(row, z - 1, lam * wz)
        add(row, z, -2.0 * lam * wz)
        add(row, z + 1, lam * wz)
        rhs.append(0.0)
        row += 1

    add(row, 128, 1.0)
    rhs.append(0.0)
    row += 1

    n_cols = 256 + n_pix
    a = csr_matrix((data, (rows, cols)), shape=(row, n_cols))
    result = lsqr(a, np.asarray(rhs), atol=1e-8, btol=1e-8, iter_lim=2000)
    return result[0][:256]


def estimate_response(images, exposures, n_samples=220, lam=25.0,
                      pixel_weights=None, static_mask=None, seed=0):
    """Estimate inverse response g(z) per channel.

    Parameters
    ----------
    images : (N, H, W, 3) uint8
    exposures : (N,) exposure times
    pixel_weights : optional (N, H, W) confidence in [0, 1], used to downweight
        moving / inconsistent observations during fitting.

    Returns
    -------
    g : (256, 3) estimated log inverse response, anchored at g(128) = 0.
    """
    images = np.asarray(images)
    n_frames, h, w, _ = images.shape
    rng = np.random.default_rng(seed)
    py, px = _sample_positions(images, n_samples, rng, static_mask)
    log_t = np.log(np.asarray(exposures, dtype=np.float64))

    g = np.zeros((256, 3))
    for c in range(3):
        z = images[:, py, px, c].T.astype(np.int64)
        w = hat_weights(z)
        if pixel_weights is not None:
            w = w * np.clip(pixel_weights[:, py, px].T, 0.0, 1.0)
        g[:, c] = _build_and_solve(z, log_t, lam, w)
        g[:, c] -= g[128, c]
        # Only flatten non-monotonic swings inside intensity ranges that have
        # no data support; well-observed levels keep their least-squares value.
        observed = np.zeros(256, dtype=bool)
        observed[np.unique(z)] = True
        g[:, c] = _repair_unsupported(g[:, c], observed)
        g[:, c] -= g[128, c]
    return g


def _repair_unsupported(curve, observed):
    """Clamp unsupported levels to the nearest supported envelope.

    The least-squares curve is trustworthy where data exist; in gaps (and at
    the bright clipped end) the smoothness prior can still overshoot, so we
    clamp each unsupported point to the bracketing supported values, keeping
    the result monotone without touching fitted points.
    """
    out = curve.copy()
    supported = np.flatnonzero(observed)
    for z in range(256):
        if observed[z]:
            continue
        left = supported[supported < z]
        right = supported[supported > z]
        lo = curve[left.max()] if left.size else -np.inf
        hi = curve[right.min()] if right.size else np.inf
        out[z] = min(max(curve[z], lo), hi)
    return out
