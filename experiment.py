"""Pitch-shifting experiment with the phase vocoder.

Generates a harmonic stack tone and a percussive knock, shifts both up
and down by a configurable number of semitones, and verifies frequency
ratios, transient spread, and constant duration. Zero semitones is an
exact bypass. Writes WAV files and figures into artifacts/.
"""

import argparse
import os

import numpy as np
from scipy.io import wavfile

from phase_vocoder import pitch_shift, stft

SR = 44100
ART = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")


def harmonic_tone(sr=SR, dur=2.0, f0=220.0, n_harm=8):
    """Harmonic stack with decaying partial amplitudes."""
    t = np.arange(int(sr * dur)) / sr
    x = np.zeros_like(t)
    for h in range(1, n_harm + 1):
        x += (1.0 / h) * np.sin(2 * np.pi * f0 * h * t)
    env = np.minimum(1.0, t / 0.02) * np.minimum(1.0, (dur - t) / 0.05)
    x *= env
    return 0.6 * x / np.max(np.abs(x))


def knock(sr=SR, dur=2.0, n_hits=4):
    """Percussive clicks: decaying band-limited noise bursts."""
    n = int(sr * dur)
    x = np.zeros(n)
    rng = np.random.default_rng(7)
    for k in range(n_hits):
        start = int((0.25 + 0.45 * k) * sr)
        blen = int(0.09 * sr)
        t = np.arange(blen) / sr
        burst = rng.standard_normal(blen) * np.exp(-t / 0.012)
        spec = np.fft.rfft(burst)
        freqs = np.fft.rfftfreq(blen, 1 / sr)
        spec *= np.exp(-((freqs - 1800.0) / 1500.0) ** 2)
        x[start:start + blen] += np.fft.irfft(spec, n=blen)
    return 0.8 * x / np.max(np.abs(x))


def save_wav(name, x, sr=SR):
    os.makedirs(ART, exist_ok=True)
    y = np.clip(x, -1.0, 1.0)
    wavfile.write(os.path.join(ART, name), sr,
                  (y * 32767).astype(np.int16))


def track_peaks(x, sr=SR, n_fft=4096, hop=1024, n_peaks=8, fmin=60.0):
    """Per-frame spectral peak frequencies via parabolic interpolation.

    Returns (times, peaks) where peaks is (n_frames, n_peaks) in Hz,
    with 0.0 where a peak slot is empty.
    """
    window = np.hanning(n_fft)
    mag = np.abs(stft(x, n_fft, hop, window))
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    times = (np.arange(mag.shape[0]) * hop + n_fft / 2) / sr
    peaks = np.zeros((mag.shape[0], n_peaks))
    lo = np.searchsorted(freqs, fmin)
    for i, row in enumerate(mag):
        cand = []
        for b in range(max(1, lo), len(row) - 1):
            if row[b] > row[b - 1] and row[b] >= row[b + 1]:
                cand.append(b)
        cand.sort(key=lambda b: row[b], reverse=True)
        chosen = []
        for b in cand:
            if all(abs(b - c) > 4 for c in chosen):
                chosen.append(b)
            if len(chosen) == n_peaks:
                break
        est = []
        for b in chosen:
            a, m, c = np.log(row[b - 1] + 1e-12), np.log(row[b] + 1e-12), \
                np.log(row[b + 1] + 1e-12)
            denom = a - 2 * m + c
            off = 0.5 * (a - c) / denom if abs(denom) > 1e-12 else 0.0
            est.append((b + np.clip(off, -0.5, 0.5)) * sr / n_fft)
        peaks[i, :len(est)] = sorted(est)
    return times, peaks


def frequency_ratio_report(x_ref, x_shifted, semitones, sr=SR):
    """Median ratio of matched peak tracks vs the theoretical ratio."""
    _, p_ref = track_peaks(x_ref, sr)
    _, p_out = track_peaks(x_shifted, sr)
    expected = 2.0 ** (semitones / 12.0)
    ratios = []
    for k in range(p_ref.shape[1]):
        mask = (p_ref[:, k] > 0) & (p_out[:, k] > 0)
        if mask.sum() > 5:
            ratios.append(np.median(p_out[mask, k] / p_ref[mask, k]))
    ratios = np.array(ratios)
    med = np.median(ratios)
    err_cents = 1200 * np.log2(med / expected)
    return med, expected, err_cents


def transient_spread(x, sr=SR, win=0.06):
    """Leading-edge smear metrics around the loudest onset.

    Returns (pre_echo_db, rise_ms):
      pre_echo_db - RMS in the 60 ms ending 5 ms before the -40 dB
        crossing of the attack, relative to the 60 ms after the
        -6 dB crossing. Smearing pushes energy ahead of the onset,
        raising this ratio.
      rise_ms - time for the 2 ms RMS envelope to climb from -40 dB
        to -6 dB relative to its peak. Crisp transient: ~2 ms.
    """
    w = int(0.002 * sr)
    env = np.sqrt(np.convolve(x ** 2, np.ones(w) / w, mode="same"))
    peak = env.max()
    i_peak = int(np.argmax(env))
    lo = peak * 10 ** (-40.0 / 20.0)
    hi = peak * 10 ** (-6.0 / 20.0)
    i_lo = int(np.argmax(env[:i_peak + 1] > lo))
    i_hi = int(np.argmax(env[:i_peak + 1] > hi))
    rise_ms = (i_hi - i_lo) / sr * 1000.0

    n = int(win * sr)
    g = int(0.005 * sr)
    pre = x[max(0, i_lo - n):max(1, i_lo - g)]
    post = x[i_hi:i_hi + n]
    pre_rms = np.sqrt(np.mean(pre ** 2) + 1e-18)
    post_rms = np.sqrt(np.mean(post ** 2) + 1e-18)
    pre_db = 20 * np.log10(pre_rms / post_rms)
    return pre_db, rise_ms


def plot_peak_tracks(datasets, semitones, path):
    """Spectrogram + spectral peak trajectories for original/up/down."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.5), sharey=True)
    for ax, (title, x) in zip(axes, datasets):
        times, peaks = track_peaks(x)
        n_fft, hop = 2048, 512
        window = np.hanning(n_fft)
        mag = np.abs(stft(x, n_fft, hop, window))
        db = 20 * np.log10(mag.T + 1e-9)
        t_spec = (np.arange(mag.shape[0]) * hop + n_fft / 2) / SR
        f_spec = np.fft.rfftfreq(n_fft, 1 / SR)
        ax.pcolormesh(t_spec, f_spec, db, shading="auto",
                      cmap="magma", vmin=db.max() - 70, vmax=db.max())
        for k in range(peaks.shape[1]):
            m = peaks[:, k] > 0
            ax.plot(times[m], peaks[m, k], ".", ms=2, color="cyan")
        ax.set_ylim(0, 6000)
        ax.set_title(title)
        ax.set_xlabel("time (s)")
    axes[0].set_ylabel("frequency (Hz)")
    fig.suptitle(f"Spectral peak trajectories (shift = ±{semitones} st)")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--semitones", type=float, default=4.0,
                    help="shift up/down by this many semitones")
    args = ap.parse_args()
    st = args.semitones

    tone = harmonic_tone()
    hit = knock()
    save_wav("input_harmonic.wav", tone)
    save_wav("input_knock.wav", hit)

    print("== bypass check (0 semitones) ==")
    assert np.array_equal(pitch_shift(tone, 0, SR), tone)
    assert np.array_equal(pitch_shift(hit, 0, SR), hit)
    print("0 st returns the original samples exactly: OK")

    print(f"\n== duration check (±{st} st) ==")
    outs = {}
    for name, x in (("harmonic", tone), ("knock", hit)):
        for sign, tag in ((+1, "up"), (-1, "down")):
            for lock in (False, True):
                y = pitch_shift(x, sign * st, SR, transient_lock=lock)
                assert len(y) == len(x), (name, tag, lock)
                key = (name, tag, lock)
                outs[key] = y
                suffix = "_lock" if lock else ""
                save_wav(f"{name}_{tag}{suffix}.wav", y)
    print(f"all outputs keep {len(tone)} samples ("
          f"{len(tone) / SR:.3f} s @ {SR} Hz): OK")

    print(f"\n== frequency ratio check (harmonic tone, ±{st} st) ==")
    for tag in ("up", "down"):
        y = outs[("harmonic", tag, True)]
        med, exp, cents = frequency_ratio_report(
            tone, y, st if tag == "up" else -st)
        print(f"{tag:4s}: measured ratio {med:.4f}, "
              f"expected {exp:.4f}, error {cents:+.1f} cents")

    print("\n== transient spread (knock, per-output onset alignment) ==")
    pre_db, rise_ms = transient_spread(hit)
    print(f"original: pre-echo {pre_db:6.1f} dB, rise {rise_ms:5.1f} ms")
    for tag in ("up", "down"):
        for lock in (False, True):
            y = outs[("knock", tag, lock)]
            pre_db, rise_ms = transient_spread(y)
            print(f"{tag:4s} lock={'on ' if lock else 'off'}: "
                  f"pre-echo {pre_db:6.1f} dB, rise {rise_ms:5.1f} ms")

    plot_peak_tracks(
        [("original", tone),
         (f"+{st} st", outs[("harmonic", "up", True)]),
         (f"-{st} st", outs[("harmonic", "down", True)])],
        st, os.path.join(ART, "peak_tracks.png"))
    plot_transient_envelopes(
        [("original", hit),
         (f"+{st} st lock off", outs[("knock", "up", False)]),
         (f"+{st} st lock on", outs[("knock", "up", True)])],
        0.25, os.path.join(ART, "transient_envelopes.png"))
    print(f"\nfigures + WAVs written to {ART}/")


def plot_transient_envelopes(curves, onset_s, path):
    """Energy envelopes around the first knock onset, lock on vs off."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 4))
    half = 0.08
    for label, x in curves:
        c = int(onset_s * SR)
        n = int(half * SR)
        seg = x[c - n:c + n]
        t = (np.arange(len(seg)) - n) / SR * 1000.0
        ax.plot(t, np.abs(seg), label=label, lw=1)
    ax.axvline(0, color="k", ls="--", lw=0.8)
    ax.set_xlabel("time relative to onset (ms)")
    ax.set_ylabel("|amplitude|")
    ax.set_title("Transient spread around onset (phase smearing check)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    main()
