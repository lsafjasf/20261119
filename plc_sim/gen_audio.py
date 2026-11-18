"""Synthesize a test signal: concatenated vowels and fricatives.

Vowels are produced by a glottal pulse train through parallel formant
resonators; fricatives are shaped noise. 16 kHz mono float32.
"""
import numpy as np

FS = 16000


def _glottal_source(dur, f0=120.0):
    n = int(dur * FS)
    phase = np.cumsum(np.full(n, f0 / FS)) % 1.0
    duty = 0.4
    pulse = np.where(phase < duty,
                     0.5 * (1 - np.cos(2 * np.pi * phase / duty)),
                     0.0)
    src = np.diff(pulse, prepend=pulse[0])
    return src.astype(np.float64)


def _resonator(x, f, bw):
    """Two-pole resonator filter."""
    r = np.exp(-np.pi * bw / FS)
    theta = 2 * np.pi * f / FS
    a1 = -2 * r * np.cos(theta)
    a2 = r * r
    y = np.zeros_like(x)
    for i in range(2, len(x)):
        y[i] = x[i] - a1 * y[i - 1] - a2 * y[i - 2]
    return y


def vowel(dur, formants, f0=120.0):
    src = _glottal_source(dur, f0)
    out = np.zeros_like(src)
    for f, bw, amp in formants:
        out += amp * _resonator(src, f, bw)
    out /= np.max(np.abs(out)) + 1e-12
    return out


def fricative(dur, center, bw):
    n = int(dur * FS)
    rng = np.random.default_rng(7)
    noise = rng.standard_normal(n)
    y = _resonator(noise, center, bw)
    y /= np.max(np.abs(y)) + 1e-12
    return y


def _xfade(a, b, n=80):
    w = 0.5 * (1 - np.cos(np.pi * np.arange(n) / n))
    mid = a[-n:] * (1 - w) + b[:n] * w
    return np.concatenate([a[:-n], mid, b[n:]])


SEGMENTS = [
    ("a", vowel(0.35, [(730, 90, 1.0), (1090, 110, 0.5), (2440, 170, 0.25)])),
    ("s", fricative(0.35, 6000, 1500)),
    ("i", vowel(0.35, [(270, 90, 1.0), (2290, 110, 0.6), (3010, 170, 0.3)])),
    ("sh", fricative(0.35, 3000, 1200)),
    ("u", vowel(0.35, [(300, 90, 1.0), (870, 110, 0.5), (2240, 170, 0.2)])),
]


def build():
    sig = SEGMENTS[0][1]
    for name, seg in SEGMENTS[1:]:
        sig = _xfade(sig, seg)
    sig = 0.6 * sig / np.max(np.abs(sig))
    head = np.zeros(int(0.05 * FS))
    sig = np.concatenate([head, sig, head])
    return sig.astype(np.float32)


if __name__ == "__main__":
    import wave
    s = build()
    pcm = (s * 32767).astype(np.int16)
    with wave.open("out/source.wav", "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(pcm.tobytes())
    print(f"source.wav: {len(s)} samples, {len(s)/FS:.3f} s")
