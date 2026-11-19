"""Streaming glottal-source / formant-cascade vowel synthesizer.

Signal chain:

    Rosenberg-C periodic pulse  --+
                                  +-- source --[ R1 ][ R2 ][ R3 ]--> vowel
    Gaussian aspiration noise ----+

All filter sections are direct-form-II-transposed biquads whose state is kept
across blocks. Parameter changes only update the *coefficients*; the internal
states and the phase accumulator are never reset, so moving a control produces
a smooth transition instead of retriggering the audio.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

from .params import SAMPLE_RATE, SynthParams, clamp_params

# Rosenberg "C" pulse shape constants.
OPEN_QUOTIENT = 0.60       # fraction of the period the glottis is open
RETURN_RATIO = 0.40        # fraction of the open phase used for closing

# Maximum allowed pole radius. Staying strictly inside the unit circle is what
# guarantees that every coefficient update remains stable.
MAX_RADIUS = 0.9995


def resonator_coeffs(freq: float, bandwidth: float, fs: int):
    """Return (b, a) biquad coefficients for a 2-pole Klatt resonator.

    The numerator is a single scaled feed-forward tap (b0); b1 = b2 = 0.
    The scale normalises the on-resonance gain to ~1 for each section.
    """
    freq = float(np.clip(freq, 10.0, fs * 0.48))
    bandwidth = float(np.clip(bandwidth, 10.0, fs * 0.45))
    r = float(np.exp(-np.pi * bandwidth / fs))
    r = min(r, MAX_RADIUS)
    theta = 2.0 * np.pi * freq / fs
    # |1 - a1 z^-1 - a2 z^-2| evaluated on the unit circle at angle theta.
    denom_mag = abs(
        (1.0 - r * np.exp(1j * theta))
        * (1.0 - r * np.exp(-1j * theta))
    )
    b0 = denom_mag
    a1 = -2.0 * r * np.cos(theta)
    a2 = r * r
    return (b0, 0.0, 0.0), (1.0, a1, a2)


class _Biquad:
    """Direct-form-II-transposed section with persistent state."""

    __slots__ = ("b", "a", "zi", "ramp_history")

    def __init__(self):
        self.b = (1.0, 0.0, 0.0)
        self.a = (1.0, 0.0, 0.0)
        self.zi = np.zeros(2, dtype=np.float64)
        self.ramp_history = []

    def set_coeffs(self, b, a):
        self.b, self.a = b, a

    def process(self, x: np.ndarray) -> np.ndarray:
        y, self.zi = lfilter(self.b, self.a, x, zi=self.zi)
        return y

    def process_ramped(self, x: np.ndarray, start_coeffs, end_coeffs,
                       segments: int):
        """Apply coefficients that ramp linearly within one block.

        Splitting the block into short segments and interpolating the biquad
        taps between them removes the per-block coefficient-switch zipper.
        Every intermediate coefficient set is built from in-range pole
        parameters, so the section stays stable throughout the ramp.
        """
        b0_s, a1_s, a2_s = start_coeffs
        b0_e, a1_e, a2_e = end_coeffs
        pieces = []
        edges = np.linspace(0, x.shape[0], segments + 1, dtype=int)
        for seg in range(segments):
            frac = seg / max(1, segments - 1)
            b0 = b0_s + frac * (b0_e - b0_s)
            a1 = a1_s + frac * (a1_e - a1_s)
            a2 = a2_s + frac * (a2_e - a2_s)
            self.b = (b0, 0.0, 0.0)
            self.a = (1.0, a1, a2)
            self.ramp_history.append((b0, a1, a2))
            lo, hi = edges[seg], edges[seg + 1]
            if hi > lo:
                piece, self.zi = lfilter(self.b, self.a, x[lo:hi], zi=self.zi)
                pieces.append(piece)
        return np.concatenate(pieces) if pieces else np.array([], dtype=np.float64)


class VowelSynthesizer:
    """Stateful streaming synthesizer.

    Call :meth:`process_block` repeatedly with the current desired parameters.
    The object remembers oscillator phase, noise state and filter memory, so a
    continuous stream is produced regardless of how controls move.
    """

    def __init__(self, params: SynthParams | None = None, fs: int = SAMPLE_RATE,
                 block_size: int = 512, seed: int | None = 1234,
                 ramp_segments: int = 8):
        self.fs = fs
        self.block_size = block_size
        self.ramp_segments = int(ramp_segments)
        self.params = clamp_params(params or SynthParams())
        self._phase = 0.0
        self._rng = np.random.default_rng(seed)
        self._sections = [_Biquad() for _ in range(3)]
        self._smooth = self.params.to_dict()
        self._prev_coeffs = None
        self.sample_index = 0
        # Record of the *actual* (smoothed, applied) parameters per block.
        self.trajectory: list[dict] = []

    def reset(self):
        self._phase = 0.0
        self._rng = np.random.default_rng(self._rng.integers(0, 2**31 - 1))
        for sec in self._sections:
            sec.zi[:] = 0.0
        self._smooth = self.params.to_dict()
        self._prev_coeffs = None
        self.sample_index = 0
        self.trajectory.clear()

    def _smoothed(self, n: int) -> dict:
        """One-pole interpolation toward the declared target parameters."""
        tau = max(self.params.smooth_tau, 1e-4)
        coeff = float(np.exp(-n / (tau * self.fs)))
        targets = self.params.to_dict()
        applied = {}
        for key, target in targets.items():
            applied[key] = self._smooth[key] + coeff * (target - self._smooth[key])
        self._smooth = applied
        return applied

    def _glottal_pulse(self, phase: np.ndarray) -> np.ndarray:
        """Rosenberg-C waveform from normalised phase in [0, 1)."""
        t_open = OPEN_QUOTIENT
        t_return = t_open * RETURN_RATIO
        t1 = t_open - t_return
        out = np.zeros_like(phase)
        rising = phase < t1
        closing = (phase >= t1) & (phase < t_open)
        out[rising] = phase[rising] / t1
        out[closing] = 1.0 - (phase[closing] - t1) / t_return
        return out

    def process_block(self, params: SynthParams | None = None,
                      n: int | None = None):
        """Synthesise one block; returns (source, output) float arrays."""
        if params is not None:
            self.params = clamp_params(params)
        n = int(n or self.block_size)
        applied = self._smoothed(n)

        # Phase accumulation with the (smoothed) instantaneous f0.
        f0 = applied["f0"]
        phase_inc = f0 / self.fs
        phases = (self._phase + (np.arange(n) + 1) * phase_inc) % 1.0
        self._phase = float(phases[-1])
        periodic = self._glottal_pulse(phases)

        noise = self._rng.standard_normal(n)
        breath = applied["breathiness"]
        voiced_w = np.sqrt(max(0.0, 1.0 - breath * breath))
        source = voiced_w * periodic + breath * noise

        formants = [
            (applied["f1"], applied["bw1"]),
            (applied["f2"], applied["bw2"]),
            (applied["f3"], applied["bw3"]),
        ]
        output = source
        end_coeffs = []
        for idx, (section, (fc, bw)) in enumerate(zip(self._sections, formants)):
            b, a = resonator_coeffs(fc, bw, self.fs)
            end = (b[0], a[1], a[2])
            end_coeffs.append(end)
            if self._prev_coeffs is None:
                start = end
            else:
                start = self._prev_coeffs[idx]
            section.ramp_history = []
            output = section.process_ramped(output, start, end, self.ramp_segments)
        self._prev_coeffs = end_coeffs

        # Gentle peak limiter keeps the exported WAV inside [-1, 1].
        peak = float(np.max(np.abs(output))) if output.size else 0.0
        if peak > 0.95:
            output = output * (0.95 / peak)

        self.trajectory.append(
            {"time": self.sample_index / self.fs, "block": n, **applied}
        )
        self.sample_index += n
        return source.astype(np.float64), output.astype(np.float64)
