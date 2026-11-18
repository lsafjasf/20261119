"""Phase-vocoder pitch shifting with optional transient locking.

Pitch shift = phase-vocoder time stretch by r = 2**(semitones/12),
then resample back to the original number of samples. The WAV
sample-rate tag is never touched, and zero semitones is an exact
bypass returning the input samples unchanged.
"""

import numpy as np


def stft(x, n_fft, hop, window):
    """Frame x -> complex STFT, shape (n_frames, n_fft//2 + 1)."""
    n_frames = 1 + max(0, (len(x) - n_fft) // hop)
    frames = np.lib.stride_tricks.as_strided(
        x,
        shape=(n_frames, n_fft),
        strides=(x.strides[0] * hop, x.strides[0]),
    )
    return np.fft.rfft(frames * window, axis=1)


def istft(spectrogram, hop, window, length):
    """Overlap-add inverse of stft with COLA normalisation."""
    n_fft = (spectrogram.shape[1] - 1) * 2
    frames = np.fft.irfft(spectrogram, n=n_fft, axis=1)
    out = np.zeros((spectrogram.shape[0] - 1) * hop + n_fft)
    norm = np.zeros_like(out)
    win_sq = window ** 2
    for i, frame in enumerate(frames * window):
        start = i * hop
        out[start:start + n_fft] += frame
        norm[start:start + n_fft] += win_sq
    nz = norm > 1e-8
    out[nz] /= norm[nz]
    return out[:length]


def detect_transients(magnitude, threshold=1.5, smooth=17):
    """Spectral-flux onset detection; returns bool array per frame."""
    flux = np.sum(np.maximum(0.0, np.diff(magnitude, axis=0)), axis=1)
    flux = np.concatenate(([flux[0] if len(flux) else 0.0], flux))
    kernel = np.ones(smooth) / smooth
    local_mean = np.convolve(flux, kernel, mode="same") + 1e-12
    floor = 0.02 * flux.max() if len(flux) else 0.0
    return (flux > threshold * local_mean) & (flux > floor)


def time_stretch(x, rate, n_fft=2048, hop=None, transient_lock=False):
    """Phase-vocoder time stretch by `rate` (>1 = longer).

    With transient_lock=True, synthesis phase is reset to the analysis
    phase at detected transient frames (phase locking), which keeps
    onsets crisp instead of smeared.
    """
    if hop is None:
        hop = n_fft // 4
    window = np.hanning(n_fft)
    # Centre-pad so frame centres align with sample times (keeps onset
    # positions intact) and the frames cover the full signal length
    # (otherwise the stretched output comes up short and the pitch
    # ratio drifts).
    pad = n_fft // 2
    padded = np.concatenate([np.zeros(pad), x, np.zeros(n_fft)])
    spec = stft(padded, n_fft, hop, window)
    n_bins = spec.shape[1]
    omega = 2.0 * np.pi * hop * np.arange(n_bins) / n_fft

    locks = None
    lock_span = 0
    if transient_lock:
        locks = detect_transients(np.abs(spec))
        # An output frame's OLA influence spans n_fft/hop frames, so
        # the locked region must cover that whole span around each
        # transient for the attack to resynthesise coherently.
        lock_span = n_fft // hop

    n_out_frames = int(np.round((spec.shape[0] - 1) * rate)) + 1
    out = np.zeros((n_out_frames, n_bins), dtype=complex)

    phase_acc = np.angle(spec[0])
    out[0] = spec[0]
    t_float = 0.0
    for i in range(1, n_out_frames):
        t_float += 1.0 / rate
        t0 = int(np.floor(t_float))
        t1 = min(t0 + 1, spec.shape[0] - 1)
        frac = t_float - t0
        mag = (1.0 - frac) * np.abs(spec[t0]) + frac * np.abs(spec[t1])

        if locks is not None:
            lo = max(0, t0 - lock_span)
            hi = min(len(locks), t1 + lock_span + 1)
            if locks[lo:hi].any():
                # Transient locking: render the nearest analysis frame
                # directly (no cross-frame interpolation), shifted by
                # linear phase to its natural output position
                # t*rate*hop. Inside the locked region this approaches
                # identity resynthesis, so the attack stays crisp and
                # correctly placed.
                t_near = int(np.round(t_float))
                t_near = min(max(t_near, 0), spec.shape[0] - 1)
                shift = (t_near * rate - i) * hop
                phasor = np.exp(
                    -2j * np.pi * np.arange(n_bins) * shift / n_fft)
                out[i] = spec[t_near] * phasor
                phase_acc = np.angle(out[i])
                continue

        delta = np.angle(spec[t1]) - np.angle(spec[t0]) - omega
        delta = delta - 2.0 * np.pi * np.round(delta / (2.0 * np.pi))
        phase_acc += omega + delta

        out[i] = mag * np.exp(1j * phase_acc)

    out_len = max(int(np.round(len(x) * rate)), 1)
    y = istft(out, hop, window, int(np.round(pad * rate)) + out_len)
    return y[int(np.round(pad * rate)):]


def resample_to_length(x, length):
    """Linear-interpolation resample to exactly `length` samples."""
    if len(x) == length:
        return x.copy()
    src = np.linspace(0.0, 1.0, num=len(x), endpoint=True)
    dst = np.linspace(0.0, 1.0, num=length, endpoint=True)
    return np.interp(dst, src, x)


def pitch_shift(x, semitones, sr, n_fft=2048, transient_lock=False):
    """Shift pitch by `semitones`; output has exactly len(x) samples.

    semitones == 0 is a pure bypass: the input samples are returned
    unchanged (no resynthesis artefacts).
    """
    x = np.asarray(x, dtype=np.float64)
    if semitones == 0:
        return x.copy()
    rate = 2.0 ** (semitones / 12.0)
    stretched = time_stretch(x, rate, n_fft=n_fft,
                             transient_lock=transient_lock)
    return resample_to_length(stretched, len(x))
