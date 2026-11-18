"""Objective metrics for PLC evaluation."""
import numpy as np

FS = 16000


def region_rmse(ref, test, lo, hi):
    """RMSE and SNR(dB) of test vs ref inside [lo, hi)."""
    r = ref[lo:hi].astype(np.float64)
    t = test[lo:hi].astype(np.float64)
    err = t - r
    rmse = float(np.sqrt(np.mean(err ** 2)))
    sig = float(np.sqrt(np.mean(r ** 2))) + 1e-12
    return rmse, 20 * np.log10(sig / (rmse + 1e-12))


def log_spectral_distance(ref, test, lo, hi, win=160, hop=80):
    """Mean log-spectral distance (dB) over frames inside [lo, hi)."""
    r = ref[lo:hi].astype(np.float64)
    t = test[lo:hi].astype(np.float64)
    w = np.hanning(win)
    vals = []
    for s in range(0, len(r) - win + 1, hop):
        R = np.abs(np.fft.rfft(r[s:s + win] * w)) + 1e-8
        T = np.abs(np.fft.rfft(t[s:s + win] * w)) + 1e-8
        d = 20 * np.log10(R) - 20 * np.log10(T)
        vals.append(np.sqrt(np.mean(d ** 2)))
    return float(np.mean(vals)) if vals else float("nan")


def junction_metrics(ref, test, boundary, half=64):
    """Residual click measures at a junction (seam) sample index.

    click_step: |x[b] - x[b-1]|, first difference exactly at the seam
        (reference-free).  Large for hard-junction concealment.
    click_hf_dbfs: 4-8 kHz band energy in a +-4 ms window around the
        seam, dBFS absolute.
    hf_excess_db: same band energy relative to the reference at the
        same location (positive = concealment adds HF splash).
    """
    t = test.astype(np.float64)
    b = int(np.clip(boundary, 1, len(t) - 1))
    click_step = float(abs(t[b] - t[b - 1]))

    from scipy.signal import butter, sosfiltfilt
    sos = butter(4, 4000 / (FS / 2), btype="high", output="sos")
    r = ref.astype(np.float64)
    wlo, whi = max(0, boundary - half), min(len(t), boundary + half)
    et = float(np.sum(sosfiltfilt(sos, t[wlo:whi]) ** 2)) + 1e-12
    er = float(np.sum(sosfiltfilt(sos, r[wlo:whi]) ** 2)) + 1e-12
    return click_step, 10 * np.log10(et), 10 * np.log10(et / er)


def segment_bounds(n_frames, lost_frames, frame=320):
    """Sample range covered by the lost frames."""
    lo = min(lost_frames) * frame
    hi = (max(lost_frames) + 1) * frame
    return lo, hi
