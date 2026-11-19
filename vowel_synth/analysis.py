"""Spectral analysis, declared-vs-detected formant measurement and voicing.

Formant detection is *anchor based*: the user-declared formant frequencies are
treated as prior centres, and the nearest local maximum of the measured output
spectrum is located around each one. Its -3 dB bandwidth is then read directly
from the magnitude envelope. This keeps the measurement tied to the declared
parameters instead of running an untracked blind peak search.
"""

from __future__ import annotations

import numpy as np
def lpc_envelope(x: np.ndarray, fs: int, order: int = 12, nfft: int = 2048):
    """All-pole spectral envelope via autocorrelation LPC.

    The LPC envelope smooths the harmonic comb of the periodic source so the
    formant resonance shape (and its -3 dB width) can be measured cleanly.
    """
    if x.shape[0] <= order + 2:
        order = max(2, x.shape[0] - 3)
    windowed = x * np.hanning(x.shape[0])
    autocorr = np.correlate(windowed, windowed, mode="full")[x.shape[0] - 1:]
    autocorr = autocorr / (autocorr[0] + 1e-12)
    lpc_coeffs = _levinson(autocorr, order)
    gains = np.concatenate(([1.0], lpc_coeffs))
    envelope = np.abs(np.fft.rfft(gains, n=nfft))
    env_db = -20.0 * np.log10(envelope + 1e-9)
    freqs = np.fft.rfftfreq(nfft, 1 / fs)
    return freqs, env_db


def _levinson(autocorr, order):
    """Levinson-Durbin for the LPC normal equations.

    Returns prediction coefficients ``a`` whose all-pole denominator is
    ``[1, a[0], ..., a[order-1]]``; i.e. it solves
    ``R @ a = -autocorr[1:order+1]`` for the autocorrelation Toeplitz matrix.
    """
    a = np.zeros(order)
    err = 1.0
    for i in range(order):
        reflection = -(autocorr[i + 1] + np.sum(a[:i] * autocorr[i:0:-1])) / (err + 1e-12)
        updated = a.copy()
        if i > 0:
            updated[:i] = a[:i] + reflection * a[i - 1::-1]
        updated[i] = reflection
        err *= 1.0 - reflection * reflection
        a = updated
    return a


def magnitude_spectrum(x: np.ndarray, fs: int, nfft: int = 2048):
    """Windowed single-sided magnitude spectrum in dB."""
    if x.shape[0] < 8:
        freqs = np.fft.rfftfreq(nfft, 1 / fs)
        return freqs, np.full_like(freqs, -120.0)
    window = np.hanning(x.shape[0])
    framed = x * window
    spec = np.fft.rfft(framed, n=nfft)
    mag = np.abs(spec)
    mag_db = 20.0 * np.log10(mag / (mag.max() + 1e-12) + 1e-9)
    freqs = np.fft.rfftfreq(nfft, 1 / fs)
    return freqs, mag_db


def _bandwidth_at_peak(freqs, mag_db, peak_idx):
    """-3 dB width around a peak via linear interpolation on each side."""
    target = mag_db[peak_idx] - 3.0

    def walk(step, bound):
        j = peak_idx
        while 0 <= j + step < bound and mag_db[j + step] > target:
            j += step
        k = j + step
        if not (0 <= k < bound) or mag_db[k] == mag_db[j]:
            return freqs[j]
        frac = (target - mag_db[j]) / (mag_db[k] - mag_db[j])
        return freqs[j] + frac * (freqs[k] - freqs[j])

    left = walk(-1, len(freqs))
    right = walk(1, len(freqs))
    return max(right - left, 0.0)


def detect_formants(freqs, mag_db, declared):
    """Measure peak frequency and bandwidth near each declared formant.

    ``declared`` is a sequence of (frequency, bandwidth) pairs. Each declared
    formant is matched to the nearest local maximum inside a non-overlapping
    window centred on it; its -3 dB width is read from the (LPC) envelope.
    Returns declared/detected centre frequencies and bandwidths with the error.
    """
    results = []
    declared_fs = [d[0] for d in declared]
    for i, (declared_f, declared_bw) in enumerate(declared):
        low = 0.0 if i == 0 else 0.5 * (declared_fs[i - 1] + declared_f)
        high = (len(freqs) - 1 and freqs[-1] + 1.0)
        if i + 1 < len(declared):
            high = 0.5 * (declared_f + declared_fs[i + 1])
        search = (freqs >= low) & (freqs <= high)
        if not search.any():
            results.append(_missing(declared_f, declared_bw))
            continue
        idxs = np.where(search)[0]
        local_peak = idxs[np.argmax(mag_db[idxs])]
        detected_f = float(freqs[local_peak])
        detected_bw = float(_bandwidth_at_peak(freqs, mag_db, local_peak))
        results.append(
            dict(
                declared_f=float(declared_f),
                detected_f=detected_f,
                declared_bw=float(declared_bw),
                detected_bw=detected_bw,
                error_hz=abs(detected_f - declared_f),
            )
        )
    return results


def _missing(declared_f, declared_bw):
    return dict(declared_f=float(declared_f), detected_f=np.nan,
                declared_bw=float(declared_bw), detected_bw=np.nan,
                error_hz=np.nan)


def lpc_formant_roots(x: np.ndarray, fs: int, order: int = 22,
                      max_bw: float = 1000.0):
    """Extract resonance (frequency, bandwidth) pairs from LPC pole angles.

    Poles come in conjugate pairs; each stable upper-half-plane pair yields one
    formant candidate. Bandwidth follows from the pole radius
    ``r = exp(-pi*bw/fs)``. Candidates are returned sorted by frequency.
    """
    if x.shape[0] <= order + 2:
        order = max(4, (x.shape[0] - 3) // 2 * 2)
    # First-order pre-emphasis flattens the glottal -12 dB/oct spectral tilt
    # so weak higher formants (notably in /u/) are not swamped by F1 energy.
    pre = np.empty_like(x, dtype=np.float64)
    pre[0] = x[0]
    pre[1:] = x[1:] - 0.97 * x[:-1]
    windowed = pre * np.hanning(x.shape[0])
    autocorr = np.correlate(windowed, windowed, mode="full")[x.shape[0] - 1:]
    autocorr = autocorr / (autocorr[0] + 1e-12)
    denom = np.concatenate(([1.0], _levinson(autocorr, order)))
    poles = np.roots(denom)
    candidates = []
    for pole in poles:
        radius = abs(pole)
        angle = np.angle(pole)
        if angle <= 0 or radius >= 1.0:
            continue
        freq = angle * fs / (2.0 * np.pi)
        bandwidth = -np.log(radius) * fs / np.pi
        if bandwidth <= max_bw and freq > 50:
            candidates.append((float(freq), float(bandwidth), float(radius)))
    candidates.sort()
    return candidates


def measure_formants(x: np.ndarray, fs: int, declared, order: int = 22):
    """Pair LPC pole candidates with the declared formants.

    ``declared`` is a sequence of (frequency, bandwidth) pairs. Greedy nearest
    neighbour matching on frequency keeps every measurement anchored to the
    parameter the user actually set, while the values come from the rendered
    audio rather than being echoed back.
    """
    candidates = lpc_formant_roots(x, fs, order=order)
    results = []
    used = set()
    for declared_f, declared_bw in declared:
        best_idx, best_dist = None, float("inf")
        for idx, (cand_f, cand_bw, _) in enumerate(candidates):
            if idx in used:
                continue
            # Reject spurious poles whose bandwidth is far wider than any
            # declared formant; they model spectral tilt rather than formants.
            if cand_bw > 600.0:
                continue
            dist = abs(cand_f - declared_f)
            if dist < best_dist:
                best_dist, best_idx = dist, idx
        if best_idx is None:
            results.append(_missing(declared_f, declared_bw))
            continue
        used.add(best_idx)
        detected_f, detected_bw, _ = candidates[best_idx]
        results.append(
            dict(
                declared_f=float(declared_f),
                detected_f=float(detected_f),
                declared_bw=float(declared_bw),
                detected_bw=float(detected_bw),
                error_hz=float(abs(detected_f - declared_f)),
            )
        )
    return results


def voicing_ratio(source: np.ndarray, fs: int, f0: float):
    """Estimate the periodic vs stochastic energy split of the source.

    A comb filter at the declared f0 separates harmonically-locked energy from
    the residual. Returns harmonic-to-noise ratio in dB plus the fractional
    voiced and breathy energy shares (each in [0, 1], summing to 1).
    """
    n = source.shape[0]
    if n < 4 or f0 <= 0:
        return dict(hnr_db=np.nan, voiced_share=np.nan, breathy_share=np.nan)
    lag = int(round(fs / f0))
    if lag < 1 or lag >= n:
        return dict(hnr_db=np.nan, voiced_share=np.nan, breathy_share=np.nan)

    # Periodic component: average of samples one period apart (comb averaging).
    periodic = np.empty(n - lag)
    periodic[:] = 0.5 * (source[:-lag] + source[lag:])
    residual = source[:-lag] - periodic
    p_energy = float(np.mean(periodic**2))
    n_energy = float(np.mean(residual**2)) + 1e-12
    hnr_db = 10.0 * np.log10((p_energy + 1e-12) / n_energy)
    total = p_energy + n_energy
    return dict(
        hnr_db=float(hnr_db),
        voiced_share=float(p_energy / total),
        breathy_share=float(n_energy / total),
    )
