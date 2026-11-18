"""Synthesis of three known drum voices and mixture generation.

Drums (deterministic given parameters):
  kick  : sine with exponential pitch glide + click transient
  snare : pitched body + band-passed noise burst
  hihat : high-passed noise burst with fast decay

`timbre` is a dict of multiplicative perturbations so the timbre-drift
experiment can render voices slightly differently from the ones the
templates were learned from.
"""

import numpy as np

SR = 22050
DRUMS = ("kick", "snare", "hihat")


def _env(dur, decay, sr):
    t = np.arange(int(dur * sr)) / sr
    return np.exp(-t / decay), t


def kick(timbre=None, sr=SR):
    p = {"f0": 130.0, "f1": 48.0, "glide": 0.045, "decay": 0.16,
         "click": 0.25, "dur": 0.45}
    if timbre:
        p.update({k: p[k] * v for k, v in timbre.items() if k in p})
    env, t = _env(p["dur"], p["decay"], sr)
    f = p["f1"] + (p["f0"] - p["f1"]) * np.exp(-t / p["glide"])
    phase = 2 * np.pi * np.cumsum(f) / sr
    body = np.sin(phase) * env
    rng = np.random.default_rng(7)
    click = rng.standard_normal(body.size) * np.exp(-t / 0.004) * p["click"]
    out = body + click
    return (out / (np.abs(out).max() + 1e-12)).astype(np.float64)


def snare(timbre=None, sr=SR):
    p = {"fbody": 185.0, "decay": 0.09, "noise_decay": 0.11,
         "tone_mix": 0.55, "bp_lo": 1200.0, "bp_hi": 6500.0, "dur": 0.35}
    if timbre:
        p.update({k: p[k] * v for k, v in timbre.items() if k in p})
    env, t = _env(p["dur"], p["decay"], sr)
    tone = np.sin(2 * np.pi * p["fbody"] * t) * env
    rng = np.random.default_rng(11)
    noise = rng.standard_normal(t.size)
    nenv = np.exp(-t / p["noise_decay"])
    from scipy.signal import butter, sosfilt
    sos = butter(4, [p["bp_lo"], p["bp_hi"]], btype="band", fs=sr,
                 output="sos")
    band = sosfilt(sos, noise) * nenv
    out = p["tone_mix"] * tone + (1 - p["tone_mix"]) * band
    return (out / (np.abs(out).max() + 1e-12)).astype(np.float64)


def hihat(timbre=None, sr=SR):
    p = {"hp": 7000.0, "decay": 0.035, "dur": 0.18}
    if timbre:
        p.update({k: p[k] * v for k, v in timbre.items() if k in p})
    env, t = _env(p["dur"], p["decay"], sr)
    rng = np.random.default_rng(13)
    noise = rng.standard_normal(t.size)
    from scipy.signal import butter, sosfilt
    sos = butter(4, p["hp"], btype="high", fs=sr, output="sos")
    out = sosfilt(sos, noise) * env
    return (out / (np.abs(out).max() + 1e-12)).astype(np.float64)


SYNTH = {"kick": kick, "snare": snare, "hihat": hihat}


def render_stems(events, dur, timbres=None, sr=SR):
    """Render per-drum stems and the mixture from an event list.

    events: list of (time_sec, drum_name). Returns (mix, stems).
    The event list is ground truth for evaluation only; the
    separation code never sees it.
    """
    n = int(dur * sr)
    stems = {d: np.zeros(n) for d in DRUMS}
    for t0, name in events:
        hit = SYNTH[name]((timbres or {}).get(name), sr)
        i = int(t0 * sr)
        j = min(n, i + hit.size)
        stems[name][i:j] += hit[: j - i]
    mix = sum(stems.values())
    peak = np.abs(mix).max()
    if peak > 0.95:
        for d in stems:
            stems[d] *= 0.95 / peak
        mix *= 0.95 / peak
    return mix, stems
