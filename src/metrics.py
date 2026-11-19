"""PSNR metrics split by static / motion / occlusion-entry regions."""

import numpy as np


def psnr_region(ref, test, mask):
    """PSNR on masked pixels; mask is per-frame [T,H,W] in {0,1}."""
    vals = []
    for t in range(ref.shape[0]):
        m = mask[t] > 0
        if m.sum() < 16:
            continue
        mse = np.mean((ref[t][m] - test[t][m]) ** 2)
        if mse < 1e-12:
            vals.append(60.0)
        else:
            vals.append(10 * np.log10(1.0 / mse))
    return float(np.mean(vals)) if vals else np.nan


def per_frame_psnr(ref, test, mask):
    out = np.full(ref.shape[0], np.nan, np.float32)
    for t in range(ref.shape[0]):
        m = mask[t] > 0
        if m.sum() < 16:
            continue
        mse = np.mean((ref[t][m] - test[t][m]) ** 2)
        out[t] = 60.0 if mse < 1e-12 else 10 * np.log10(1.0 / mse)
    return out


def noise_std(seq, gt):
    """Actual standard deviation of seq - gt."""
    return float((seq - gt).std())


def hf_fidelity(seq, gt, mask, sigma=1.5):
    """High-frequency correlation and amplitude gain vs. clean reference.

    gain near 1.0 means texture amplitude is preserved; gain well below 1
    indicates plastic-like over-smoothing. Only masked pixels are used.
    """
    import cv2

    def hf(x):
        return x - cv2.GaussianBlur(x, (0, 0), sigma)

    a, b = [], []
    for t in range(1, gt.shape[0]):
        m = mask[t] > 0
        if m.sum() > 16:
            a.append(hf(seq[t])[m])
            b.append(hf(gt[t])[m])
    a, b = np.concatenate(a), np.concatenate(b)
    return {
        "hf_correlation": float(np.corrcoef(a, b)[0, 1]),
        "hf_gain": float((a @ b) / (b @ b)),
    }


def all_psnr(d, seq, label):
    return {
        "label": label,
        "static": psnr_region(d["gt"], seq, d["static"]),
        "motion": psnr_region(d["gt"], seq, d["motion"]),
        "occ": psnr_region(d["gt"], seq, d["occ"]),
        "global": psnr_region(d["gt"], seq, np.ones_like(d["static"])),
    }
