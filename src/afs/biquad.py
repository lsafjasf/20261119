"""RBJ bi-quad filters and a bank of parametric notch filters.

Everything operates sample-by-sample on real one-dimensional signals so the
same objects can be driven from a block-based closed-loop simulator.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


def design_notch(f0: float, q: float, fs: float) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(b, a)`` coefficients for a notch at ``f0`` Hz.

    Uses the bandwidth quality-factor convention (identical to
    ``scipy.signal.iirnotch``), so ``Q = f0 / (-3 dB bandwidth)``.
    """
    if not 0.0 < f0 < fs / 2.0:
        raise ValueError(f"f0={f0} must lie inside (0, Nyquist={fs / 2:g})")
    if q <= 0.0:
        raise ValueError("Q must be positive")
    # Orfanidis "Introduction to Signal Processing" notch design, identical to
    # scipy.signal.iirnotch: Q = f0 / (-3 dB bandwidth).
    w0 = 2.0 * math.pi * f0 / fs
    bandwidth = w0 / q
    beta = math.tan(bandwidth / 2.0)
    gain = 1.0 / (1.0 + beta)
    b = gain * np.array([1.0, -2.0 * math.cos(w0), 1.0])
    a = np.array(
        [1.0, -2.0 * gain * math.cos(w0), (2.0 * gain - 1.0)]
    )
    return b, a


class BiquadFilter:
    """Direct-form-I bi-quad with mutable coefficients (no zipper smoothing)."""

    def __init__(self, fs: float):
        self.fs = float(fs)
        self.b = np.array([1.0, 0.0, 0.0])
        self.a = np.array([1.0, 0.0, 0.0])
        self._x = np.zeros(2)
        self._y = np.zeros(2)

    def set_notch(self, f0: float, q: float) -> None:
        self.b, self.a = design_notch(f0, q, self.fs)

    def reset(self) -> None:
        self._x.fill(0.0)
        self._y.fill(0.0)

    def process_sample(self, x: float) -> float:
        b, a = self.b, self.a
        y = (
            b[0] * x
            + b[1] * self._x[0]
            + b[2] * self._x[1]
            - a[1] * self._y[0]
            - a[2] * self._y[1]
        )
        self._x[1] = self._x[0]
        self._x[0] = x
        self._y[1] = self._y[0]
        self._y[0] = y
        return y

    def process_block(self, block: np.ndarray) -> np.ndarray:
        out = np.empty_like(block, dtype=float)
        for n, sample in enumerate(block):
            out[n] = self.process_sample(float(sample))
        return out


@dataclass
class SlotState:
    """Public state of one notch slot, used by the dashboard and metrics."""

    active: bool = False
    f0: float = 0.0
    q: float = 0.0
    depth_db: float = 0.0
    age_blocks: int = 0


class NotchBank:
    """A fixed-capacity cascade of notch filters.

    Inactive slots are unity-gain bi-quads, so the cascade length stays
    constant while the number of live notches is bounded by ``max_notches``.
    """

    def __init__(self, max_notches: int, fs: float):
        if max_notches < 1:
            raise ValueError("max_notches must be >= 1")
        self.fs = float(fs)
        self.max_notches = int(max_notches)
        self._filters = [BiquadFilter(fs) for _ in range(self.max_notches)]
        self._states = [SlotState() for _ in range(self.max_notches)]

    @property
    def active_count(self) -> int:
        return sum(s.active for s in self._states)

    def slots(self) -> list[SlotState]:
        return list(self._states)

    def active_slots(self) -> list[SlotState]:
        return [s for s in self._states if s.active]

    def frequencies(self) -> np.ndarray:
        return np.array([s.f0 for s in self._states if s.active], dtype=float)

    def free_index(self) -> int | None:
        for idx, state in enumerate(self._states):
            if not state.active:
                return idx
        return None

    def nearest_index(self, f0: float) -> tuple[int, float]:
        """Return ``(index, distance_hz)`` for the closest active notch."""
        active = [i for i, s in enumerate(self._states) if s.active]
        if not active:
            return -1, math.inf
        idx = min(active, key=lambda i: abs(self._states[i].f0 - f0))
        return idx, abs(self._states[idx].f0 - f0)

    def set_notch(self, index: int, f0: float, q: float, depth_db: float) -> None:
        self._filters[index].set_notch(f0, q)
        state = self._states[index]
        state.active = True
        state.f0 = float(f0)
        state.q = float(q)
        state.depth_db = float(depth_db)
        state.age_blocks = 0

    def release(self, index: int) -> None:
        filt = self._filters[index]
        filt.b = np.array([1.0, 0.0, 0.0])
        filt.a = np.array([1.0, 0.0, 0.0])
        filt.reset()
        self._states[index] = SlotState()

    def age(self) -> None:
        for state in self._states:
            if state.active:
                state.age_blocks += 1

    def process_block(self, block: np.ndarray) -> np.ndarray:
        out = block.astype(float, copy=True)
        for filt in self._filters:
            out = filt.process_block(out)
        return out

    def frequency_response(self, freqs: np.ndarray) -> np.ndarray:
        """Composite magnitude response (linear) of the cascade at ``freqs``."""
        response = np.ones_like(freqs, dtype=float)
        for filt in self._filters:
            w = 2.0 * math.pi * freqs / self.fs
            z = np.exp(1j * w)
            num = filt.b[0] + filt.b[1] / z + filt.b[2] / z**2
            den = filt.a[0] + filt.a[1] / z + filt.a[2] / z**2
            response *= np.abs(num / den)
        return response
