"""STFT helpers and NMF with multiplicative updates (KL divergence).

Workflow: learn spectral templates from isolated hits, then estimate
activations on a mixture with templates either locked or retrained.
"""

import numpy as np
from scipy.signal import stft, istft

N_FFT = 1024
HOP = 256
EPS = 1e-10


def mag_spectrogram(x, sr):
    f, t, Z = stft(x, fs=sr, window="hann", nperseg=N_FFT,
                   noverlap=N_FFT - HOP, boundary="zeros", padded=True)
    return np.abs(Z), f, t


def complex_spectrogram(x, sr):
    f, t, Z = stft(x, fs=sr, window="hann", nperseg=N_FFT,
                   noverlap=N_FFT - HOP, boundary="zeros", padded=True)
    return Z, f, t


def invert_spectrogram(Z, sr, n):
    _, x = istft(Z, fs=sr, window="hann", nperseg=N_FFT,
                 noverlap=N_FFT - HOP, input_onesided=True,
                 boundary=True)
    if x.size < n:
        x = np.pad(x, (0, n - x.size))
    return x[:n]


def _mu_update(V, W, H, update_W=True, update_H=True, iters=100):
    """KL-divergence multiplicative updates."""
    W = W.copy()
    H = H.copy()
    for _ in range(iters):
        Vh = W @ H + EPS
        if update_H:
            H *= (W.T @ (V / Vh)) / (W.sum(axis=0)[:, None] + EPS)
        Vh = W @ H + EPS
        if update_W:
            W *= ((V / Vh) @ H.T) / (H.sum(axis=1)[None, :] + EPS)
    return W, H


def learn_templates(hits, rank_per_drum=2, iters=200, seed=0):
    """Learn spectral templates per drum from isolated hits.

    hits: dict drum_name -> 1-D signal of one isolated hit.
    Returns (W, labels, freqs); labels maps columns to drum names.
    """
    from .synth import SR, DRUMS
    rng = np.random.default_rng(seed)
    blocks, labels = [], []
    freqs = None
    for name in DRUMS:
        V, f, _ = mag_spectrogram(hits[name], SR)
        freqs = f
        W0 = rng.random((V.shape[0], rank_per_drum)) + 0.1
        H0 = rng.random((rank_per_drum, V.shape[1])) + 0.1
        Wk, _ = _mu_update(V, W0, H0, iters=iters)
        blocks.append(Wk)
        labels += [name] * rank_per_drum
    W = np.concatenate(blocks, axis=1)
    W /= (W.sum(axis=0, keepdims=True) + EPS)
    return W, labels, freqs


def estimate_activations(V, W0, lock_templates=True, iters=150, seed=1):
    """Factorise a mixture spectrogram given initial templates.

    lock_templates=True  -> only H is updated (supervised NMF)
    lock_templates=False -> W initialised from templates but allowed to
                            adapt to the mixture (semi-supervised)
    Returns (W, H).
    """
    rng = np.random.default_rng(seed)
    H0 = rng.random((W0.shape[1], V.shape[1])) + 0.1
    W, H = _mu_update(V, W0, H0, update_W=not lock_templates,
                      update_H=True, iters=iters)
    if not lock_templates:
        s = W.sum(axis=0, keepdims=True) + EPS
        W /= s
        H *= s.T
    return W, H


def wiener_separate(Z, W, H, labels, drums):
    """Soft-mask the mixture STFT into one complex STFT per drum."""
    Vh = W @ H + EPS
    out = {}
    for name in drums:
        cols = [i for i, lab in enumerate(labels) if lab == name]
        contrib = W[:, cols] @ H[cols, :]
        mask = contrib / Vh
        out[name] = Z * mask
    return out
