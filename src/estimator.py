"""Multi-pitch (polyphonic F0) estimator.

Pipeline per frame:
  1. Hann-windowed STFT with magnitude spectrum.
  2. Extract a peak table from the magnitude spectrum.
  3. Score each F0 on a logarithmic grid by matching its harmonic
     series to peaks (existence term + magnitude term).  A lower
     sub-octave wins over an upper octave only when enough extra
     odd-order partials exist, which resolves missing-fundamental
     vs. octave ambiguity.
  4. Iteratively cancel the best-scoring candidate.  A candidate with
     no surviving peaks of its own (shared overtones only) is rejected,
     which keeps common-harmonic voices from being double counted.

The estimator consumes only the audio file.  It never imports the
synthesis module or generation parameters.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks

F0_MIN = 70.0
F0_MAX = 800.0
GRID_CENTS = 20.0
FRAME_MS = 93.0
HOP_MS = 10.0
HARM_WEIGHT_ALPHA = 0.9
MAX_HARM = 12
PEAK_REL = 0.08
PEAK_TOL_REL = 0.03
PEAK_TOL_MIN = 8.0
MAX_FIT_ERROR = 0.012


@dataclass
class FrameResult:
    time: float
    f0: list[float]
    confidence: list[float]
    salience: np.ndarray
    explained: np.ndarray
    residual: np.ndarray
    harmonics: list


def _f0_grid() -> np.ndarray:
    ratio = 2.0 ** (GRID_CENTS / 1200.0)
    n = int(np.floor(np.log(F0_MAX / F0_MIN) / np.log(ratio))) + 1
    return F0_MIN * ratio ** np.arange(n)


def extract_peaks(spec: np.ndarray, freqs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return parabolically interpolated peak frequencies and magnitudes."""
    height = max(PEAK_REL * spec.max(), 1e-9)
    idx, props = find_peaks(spec, height=height, distance=2)
    out_f = np.zeros(idx.size)
    out_m = np.zeros(idx.size)
    bin_w = freqs[1] - freqs[0]
    for k, j in enumerate(idx):
        a, b, c = spec[j - 1] if j > 0 else 0.0, spec[j], spec[j + 1] if j + 1 < spec.size else 0.0
        denom = a + c - 2.0 * b
        delta = 0.5 * (a - c) / denom if abs(denom) > 1e-12 else 0.0
        delta = float(np.clip(delta, -1.0, 1.0))
        out_f[k] = freqs[j] + delta * bin_w
        out_m[k] = b - 0.25 * (a - c) * delta
    return out_f, out_m


def _tolerance(freq: float) -> float:
    return max(PEAK_TOL_MIN, PEAK_TOL_REL * freq)


def score_f0(
    f0: float,
    peak_f: np.ndarray,
    peak_m: np.ndarray,
    ref_peak: float,
    f_nyquist: float,
) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    """Match a harmonic series of f0 against the peak table.

    Returns score, harmonic frequencies, matched peak magnitudes, and the
    harmonic order of each match.  Magnitudes are normalized by the frame's
    strongest peak so the score is comparable across frames.
    """
    n_harm = min(MAX_HARM, int((0.9 * f_nyquist) // f0))
    orders = np.arange(1, n_harm + 1)
    har_freq = orders * f0
    mags = np.zeros(n_harm)
    if peak_f.size == 0:
        return 0.0, har_freq, mags, orders
    # Nearest peak within tolerance for every harmonic.
    diff = np.abs(peak_f[None, :] - har_freq[:, None])
    nearest = np.argmin(diff, axis=1)
    tol = np.array([_tolerance(hf) for hf in har_freq])
    within = diff[np.arange(n_harm), nearest] <= tol
    mags[within] = peak_m[nearest[within]] / ref_peak
    weights = orders.astype(float) ** (-HARM_WEIGHT_ALPHA)
    hit = (mags > 0.0).astype(float)
    score = float(np.sum(weights * (0.55 * hit + 0.45 * mags)) / np.sum(weights))
    return score, har_freq, mags, orders


def refine_f0(
    f0: float,
    peak_f: np.ndarray,
    peak_m: np.ndarray,
    f_nyquist: float,
) -> tuple[float, float, np.ndarray, np.ndarray, np.ndarray]:
    """Local search for the F0 minimizing weighted mean relative error.

    Also returns that error and the matched harmonic description.  A
    hallucinated sub-multiple cannot reach a low error at any F0 because
    no single integer series explains all its borrowed peaks.
    """
    best = None
    factor = 2.0 ** (GRID_CENTS / 1200.0)
    for step in range(-3, 4):
        trial = f0 * factor**step
        _, har_freq, mags, orders = score_f0(
            trial, peak_f, peak_m, max(float(peak_m.max()), 1e-9), f_nyquist
        )
        hit = mags > 0.0
        if hit.sum() < 3:
            continue
        w = orders[hit].astype(float) ** (-HARM_WEIGHT_ALPHA)
        errs = np.array(
            [np.min(np.abs(peak_f - har_freq[k])) / har_freq[k] for k in np.where(hit)[0]]
        )
        mean_err = float(np.sum(w * errs) / np.sum(w))
        if best is None or mean_err < best[0]:
            best = (mean_err, trial, har_freq, mags, orders)
    if best is None:
        _, har_freq, mags, orders = score_f0(
            f0, peak_f, peak_m, max(float(peak_m.max()), 1e-9), f_nyquist
        )
        return f0, 1.0, har_freq, mags, orders
    return best[1], best[0], best[2], best[3], best[4]


def owns_low_peak(
    f0: float,
    peak_f: np.ndarray,
    peak_m: np.ndarray,
    claimed: np.ndarray,
) -> bool:
    """A genuine independent voice owns an unclaimed 1st-harmonic peak.

    A sub-multiple hallucination (e.g. 130.6 = 392/3) has an empty
    fundamental, so it fails.  A missing-fundamental voice lacks its 1st
    peak but owns an unclaimed 2nd/3rd partial and is admitted here; the
    exact octave partner is added in the dedicated octave pass.
    """
    ref = max(float(peak_m.max()), 1e-9)
    for order in (1, 2):
        target = order * f0
        j = int(np.argmin(np.abs(peak_f - target)))
        if (
            np.abs(peak_f[j] - target) <= _tolerance(target)
            and peak_m[j] >= 0.2 * ref
            and not claimed[j]
        ):
            return True
    return False


def is_submultiple_of_accepted(
    f0: float,
    har_freq: np.ndarray,
    mags: np.ndarray,
    accepted_f0: list[float],
) -> bool:
    """Reject f0 when it only repeats every m-th harmonic of a real voice.

    A hallucination at f0 = P/m (m = 3,5,6,7, never a power of two) has all
    its matched peaks on m-multiple harmonics of the already accepted parent
    P.  Exact octaves (m = 2,4,8 ...) are legitimate and handled elsewhere.
    """
    matched = har_freq[mags > 0.0]
    if matched.size < 3:
        return False
    for parent in accepted_f0:
        ratio = f0 / parent
        m = round(ratio)
        if m not in (3, 5, 6, 7) or abs(ratio - m) / m > 0.03:
            continue
        orders = np.round(matched / parent).astype(int)
        on_lattice = np.abs(matched - orders * parent) <= np.array(
            [_tolerance(hf) for hf in matched]
        )
        if on_lattice.all() and np.all(orders % m == 0):
            return True
    return False


def fully_explained_by_others(
    f0: float,
    har_freq: np.ndarray,
    mags: np.ndarray,
    others: list[float],
    peak_f: np.ndarray,
    peak_m: np.ndarray,
) -> bool:
    """True when the candidate contributes no peak of its own.

    Every matched peak must lie on another accepted voice's harmonic grid.
    An UPPER empty octave (f0 = 2^k * parent, e.g. 300 = 2*150) is removed
    because it is just a strong overtone of the lower voice.  A lower
    missing-fundamental voice keeps odd partials that no other grid
    explains, so at least one peak fails the lattice loop and it survives.
    """
    matched = har_freq[mags > 0.0]
    if matched.size < 3 or not others:
        return False
    for hf in matched:
        explained = False
        for parent in others:
            order = round(hf / parent)
            if order >= 1 and abs(hf - order * parent) <= _tolerance(hf):
                explained = True
                break
        if not explained:
            return False
    # All peaks are explained.  Only remove an UPPER empty octave when its
    # lower parent is itself a complete (fundamental-present) voice: then
    # the upper octave adds no information.  If the parent is a missing-
    # fundamental voice, the upper octave is a genuine separate source
    # (e.g. 440 alongside a 220 whose 1st harmonic is absent).
    for parent in others:
        log_r = np.log2(f0 / parent)
        if (
            abs(log_r - round(log_r)) <= 0.03
            and round(log_r) >= 1
            and has_own_fundamental(parent, peak_f, peak_m)
        ):
            return True
    return False


def has_own_fundamental(f0: float, peak_f: np.ndarray, peak_m: np.ndarray) -> bool:
    """True when a real peak exists at the candidate's own fundamental."""
    ref = max(float(peak_m.max()), 1e-9)
    j = int(np.argmin(np.abs(peak_f - f0)))
    return bool(
        np.abs(peak_f[j] - f0) <= _tolerance(f0) and peak_m[j] >= 0.15 * ref
    )


def analyze_frame(
    frame: np.ndarray,
    fs: int,
    grid: np.ndarray,
    max_voices: int,
    min_score: float = 0.30,
    min_conf: float = 0.32,
) -> FrameResult:
    n = frame.size
    windowed = frame * np.hanning(n)
    spec = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    f_nyquist = fs / 2.0
    bin_w = freqs[1] - freqs[0]

    peak_f, peak_m = extract_peaks(spec, freqs)
    ref_peak = max(float(peak_m.max()), 1e-9)

    raw_sal = np.array(
        [score_f0(f0, peak_f, peak_m, ref_peak, f_nyquist)[0] for f0 in grid]
    )
    cand_idx, _ = find_peaks(raw_sal, distance=2)
    if raw_sal[0] >= raw_sal[1]:
        cand_idx = np.append(cand_idx, 0)
    cand_idx = cand_idx[raw_sal[cand_idx] >= min_score]
    # Strongest salience first: confident real voices claim peaks before
    # any weaker sub-multiple hallucination can borrow them.
    cand_idx = cand_idx[np.argsort(raw_sal[cand_idx])[::-1]]

    # ---- Stage 1: greedily accept independent voices --------------------
    # A voice is independent when it owns >=3 unclaimed harmonics, at least
    # one of them a low-order (1st/2nd) peak.  Missing-fundamental voices
    # enter through their 2nd harmonic; pure ghosts have no unclaimed peak.
    chosen: list[tuple[float, float, np.ndarray, np.ndarray]] = []
    claimed = np.zeros(peak_f.size, dtype=bool)
    for idx in cand_idx:
        f0, fit_err, har_freq, mags, orders = refine_f0(
            float(grid[idx]), peak_f, peak_m, f_nyquist
        )
        if fit_err > MAX_FIT_ERROR:
            continue
        if any(abs(np.log2(f0 / c[0])) < (1.5 * GRID_CENTS / 1200.0) for c in chosen):
            continue
        hit = mags > 0.0
        independent = np.zeros_like(hit)
        for hi in np.where(hit)[0]:
            pj = int(np.argmin(np.abs(peak_f - har_freq[hi])))
            if np.abs(peak_f[pj] - har_freq[hi]) <= _tolerance(har_freq[hi]) and not claimed[pj]:
                independent[hi] = True
        # Two valid admission paths:
        #  (a) owns an unclaimed low-order peak -> ordinary independent voice;
        #  (b) owns >=3 independent peaks including a low-order (<=4) odd
        #      partial, even though 1st/2nd are shared -> a missing-
        #      fundamental voice whose odd partials prove a separate source.
        #      Stage 2 still removes pure sub-multiple ghosts.
        low_peak = owns_low_peak(f0, peak_f, peak_m, claimed)
        odd_anchor = np.any(independent & (orders <= 6) & (orders % 2 == 1))
        if independent.sum() < 3 or not (low_peak or odd_anchor):
            continue
        w = orders.astype(float) ** (-HARM_WEIGHT_ALPHA)
        unique_ratio = float(np.sum(w[independent]) / np.sum(w[hit]))
        # Missing-fundamental voices share most partials with an octave
        # partner, so their unique-ratio is intrinsically low; accept them
        # on the strength of the independent odd partials alone.
        min_unique = 0.30 if low_peak else 0.15
        if unique_ratio < min_unique:
            continue
        conf = float(np.clip(raw_sal[idx] * (0.5 + 0.5 * unique_ratio), 0.0, 1.0))
        chosen.append((f0, conf, har_freq, mags))
        for hi in np.where(hit)[0]:
            pj = int(np.argmin(np.abs(peak_f - har_freq[hi])))
            if np.abs(peak_f[pj] - har_freq[hi]) <= _tolerance(har_freq[hi]):
                claimed[pj] = True

    # ---- Stage 2: remove hallucinations against the full voice pool -----
    all_f0 = [c[0] for c in chosen]
    cleaned: list[tuple[float, float, np.ndarray, np.ndarray]] = []
    for f0_c, conf_c, har_c, mag_c in chosen:
        _, real_har, real_mag, _ = score_f0(
            f0_c, peak_f, peak_m, ref_peak, f_nyquist
        )
        parents = [p for p in all_f0 if abs(p - f0_c) > 1e-6]
        if is_submultiple_of_accepted(f0_c, real_har, real_mag, parents):
            continue
        if fully_explained_by_others(f0_c, real_har, real_mag, parents, peak_f, peak_m):
            continue
        # Empty-octave ghost: an exact upper octave with no fundamental of
        # its own and no peak its lower octave cannot explain.  A real
        # missing-fundamental lower voice keeps independent odd partials and
        # is not an *upper* octave of anything, so it never reaches here.
        if not has_own_fundamental(f0_c, peak_f, peak_m):
            lower_octave = any(
                abs(np.log2(f0_c / p) - round(np.log2(f0_c / p))) <= 0.03
                and round(np.log2(f0_c / p)) >= 1
                for p in parents
            )
            if lower_octave:
                continue
        cleaned.append((f0_c, conf_c, har_c, mag_c))

    # Keep the highest-confidence real voices within the polyphony budget.
    cleaned.sort(key=lambda c: c[1], reverse=True)
    cleaned = cleaned[:max_voices]

    # ---- Stage 3: add exact octave partners in free slots ---------------
    accepted_f0 = [c[0] for c in cleaned]
    for idx in cand_idx:
        if len(cleaned) >= max_voices:
            break
        f0, fit_err, har_freq, mags, _ = refine_f0(
            float(grid[idx]), peak_f, peak_m, f_nyquist
        )
        if fit_err > MAX_FIT_ERROR or np.sum(mags > 0.0) < 4:
            continue
        if any(abs(np.log2(f0 / af)) < (1.5 * GRID_CENTS / 1200.0) for af in accepted_f0):
            continue
        octave_of = any(
            abs(np.log2(f0 / af) - round(np.log2(f0 / af))) <= 0.03
            and round(np.log2(f0 / af)) >= 1
            for af in accepted_f0
        )
        if not octave_of:
            continue
        # Do not resurrect an empty octave that stage 2 removed: the
        # partner must keep at least one peak the lower octave cannot
        # explain (a genuine shared-overtone two-voice octave does).
        lower_parents = [
            af for af in accepted_f0
            if abs(np.log2(f0 / af) - round(np.log2(f0 / af))) <= 0.03
            and round(np.log2(f0 / af)) >= 1
        ]
        if fully_explained_by_others(f0, har_freq, mags, lower_parents, peak_f, peak_m):
            continue
        conf = float(np.clip(raw_sal[idx], 0.0, 1.0))
        cleaned.append((f0, conf, har_freq, mags))
        accepted_f0.append(f0)

    # Confidence gate: removes low-confidence fade tails of stopped voices.
    cleaned = [c for c in cleaned if c[1] >= min_conf]

    # ---- Build explained / residual spectra for visualization -----------
    explained = np.zeros_like(spec)
    residual = spec.copy()
    used = np.zeros(peak_f.size, dtype=bool)
    for f0_c, _, har_c, mag_c in cleaned:
        hit = mag_c > 0.0
        for hf in har_c[hit]:
            pj = int(np.argmin(np.abs(peak_f - hf)))
            if np.abs(peak_f[pj] - hf) > _tolerance(hf) or used[pj]:
                continue
            used[pj] = True
            c = int(round(peak_f[pj] / bin_w))
            lo, hb = max(0, c - 1), min(spec.size, c + 2)
            removed = np.minimum(residual[lo:hb], peak_m[pj])
            explained[lo:hb] += removed
            residual[lo:hb] -= removed

    cleaned.sort(key=lambda c: c[0])
    return FrameResult(
        time=0.0,
        f0=[c[0] for c in cleaned],
        confidence=[c[1] for c in cleaned],
        salience=raw_sal,
        explained=explained,
        residual=residual,
        harmonics=[c[2].tolist() for c in cleaned],
    )


def estimate_file(
    audio: np.ndarray,
    fs: int,
    max_voices: int = 3,
) -> tuple[list[FrameResult], dict]:
    n_fft = int(round(FRAME_MS / 1000.0 * fs))
    n_hop = int(round(HOP_MS / 1000.0 * fs))
    grid = _f0_grid()
    results: list[FrameResult] = []
    n = audio.size
    for start in range(0, n - n_fft + 1, n_hop):
        frame = audio[start : start + n_fft]
        fr = analyze_frame(frame, fs, grid, max_voices)
        fr.time = (start + n_fft / 2.0) / fs
        results.append(fr)
    meta = {
        "fs": fs,
        "n_fft": n_fft,
        "n_hop": n_hop,
        "grid": grid.tolist(),
        "max_voices": max_voices,
    }
    return results, meta
