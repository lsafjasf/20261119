"""Packet-loss concealment strategies.

All strategies are strictly causal: they only see the receiver's own
output history (previously decoded or previously concealed samples),
never the reference signal.
"""
import numpy as np

FS = 16000
FRAME = 320            # 20 ms
XFADE = 80             # 5 ms boundary crossfade


def _raised_cosine(n):
    return 0.5 * (1 - np.cos(np.pi * np.arange(n) / n))


def _autocorr_lpc(x, order):
    """Levinson-Durbin on autocorrelation. Returns LPC coeffs a[0..order]."""
    x = np.asarray(x, dtype=np.float64)
    r = np.correlate(x, x, mode="full")[len(x) - 1:]
    r = r[:order + 1]
    if r[0] <= 1e-10:
        return np.concatenate([[1.0], np.zeros(order)])
    r[0] *= 1.0001  # tiny regularization
    a = np.zeros(order + 1)
    a[0] = 1.0
    e = r[0]
    for i in range(1, order + 1):
        acc = r[i] + np.dot(a[1:i], r[i - 1:0:-1])
        k = -acc / e
        a_new = a.copy()
        a_new[i] = k
        a_new[1:i] = a[1:i] + k * a[i - 1:0:-1]
        a = a_new
        e *= (1 - k * k)
        if e <= 1e-12:
            break
    return a


def _pitch_lag(x, min_lag=40, max_lag=266):
    """Autocorrelation pitch lag; returns (lag, periodicity 0..1)."""
    x = np.asarray(x, dtype=np.float64)
    x = x - np.mean(x)
    e0 = np.dot(x, x)
    if e0 < 1e-9:
        return min_lag, 0.0
    ac = np.correlate(x, x, mode="full")[len(x) - 1:]
    lo, hi = min_lag, min(max_lag, len(x) - 1)
    seg = ac[lo:hi + 1]
    lag = lo + int(np.argmax(seg))
    period = float(np.clip(ac[lag] / e0, 0.0, 1.0))
    return lag, period


def _splice(history, fill, xfade=XFADE):
    """Overlap-add `fill` onto the tail of `history` (standard OLA joint).

    Output length is len(history) + len(fill) - xfade: the last `xfade`
    samples of history are replaced by the raised-cosine blend.
    """
    history = np.asarray(history, dtype=np.float64)
    fill = np.asarray(fill, dtype=np.float64)
    n = min(xfade, len(history), len(fill))
    if n == 0:
        return np.concatenate([history, fill])
    w = _raised_cosine(n)
    blend = history[-n:] * (1 - w) + fill[:n] * w
    return np.concatenate([history[:-n], blend, fill[n:]])


def conceal_silence(history, n_samples, burst_idx=0):
    """Strategy 1: mute insertion (zeros)."""
    return np.zeros(n_samples)


def conceal_repeat(history, n_samples, burst_idx=0):
    """Strategy 2: repeat the last received frame, gentle decay on bursts."""
    frame = np.asarray(history[-FRAME:], dtype=np.float64)
    reps = int(np.ceil(n_samples / FRAME))
    fill = np.tile(frame, reps)[:n_samples]
    fill *= 0.95 ** burst_idx
    return fill


def conceal_lpc(history, n_samples, burst_idx=0):
    """Strategy 3: LPC + pitch extrapolation (Opus-style predictive PLC).

    Analysis on the last 30 ms: LPC-16 spectral envelope, pitch lag from
    autocorrelation. The residual of the analysis window is extended
    forward by pitch-period replay (attenuated) plus noise, then run
    through the LPC synthesis filter. Open-loop excitation keeps the
    extrapolation stable.
    """
    from scipy.signal import lfilter
    hist = np.asarray(history, dtype=np.float64)
    ana = hist[-480:] if len(hist) >= 480 else hist
    order = 16
    a = _autocorr_lpc(ana, order)

    res = lfilter(a, [1.0], ana)               # residual of history
    lag, _ = _pitch_lag(ana)
    # voicing strength: correlation between the last two pitch periods
    if 2 * lag <= len(ana):
        p1, p2 = ana[-2 * lag:-lag], ana[-lag:]
        num = float(np.dot(p1, p2))
        den = float(np.sqrt(np.dot(p1, p1) * np.dot(p2, p2))) + 1e-12
        periodicity = float(np.clip(num / den, 0.0, 1.0))
    else:
        periodicity = 0.0
    res_energy = np.sqrt(np.mean(res ** 2)) + 1e-12
    noise_gain = res_energy * (1.0 - periodicity)
    rng = np.random.default_rng(1234 + burst_idx)

    # extend the residual forward (open loop: never reads synth output)
    res_ext = np.concatenate([res, np.zeros(n_samples)])
    rp = len(res)
    decay = 0.98 ** burst_idx
    for i in range(n_samples):
        pos = rp + i
        per = res_ext[pos - lag] * periodicity if periodicity > 0.3 else 0.0
        res_ext[pos] = (per + noise_gain * rng.standard_normal()) * decay

    # synthesize with history as filter tail
    ext = np.concatenate([hist, np.zeros(n_samples)])
    hp = len(hist)
    for i in range(n_samples):
        pos = hp + i
        acc = res_ext[rp + i]
        for k in range(1, order + 1):
            acc -= a[k] * ext[pos - k]
        ext[pos] = acc
    out = ext[hp:]
    # energy conservation: match the level of the recent history
    # (for bursts the history already contains previous fills, so the
    # decay carries over automatically)
    ref_rms = np.sqrt(np.mean(hist[-320:] ** 2)) + 1e-12
    cur_rms = np.sqrt(np.mean(out ** 2)) + 1e-12
    return out * (ref_rms / cur_rms)


STRATEGIES = {
    "silence": conceal_silence,
    "repeat": conceal_repeat,
    "lpc_pred": conceal_lpc,
}
