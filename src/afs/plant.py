"""Simulated closed-loop sound-reinforcement acoustic path.

The feedback path models three physical ingredients, all tunable at runtime:

* loop delay (latency through A/D, processing, D/A and air propagation),
* loop gain (microphone -> amplifier -> speaker -> microphone coupling),
* a narrow acoustic resonance (a room/body mode) whose centre frequency can
  drift, which is what produces a frequency-sweeping howl.

No real audio hardware is touched: this is a pure numerical model.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass
class PlantConfig:
    fs: float = 16000.0
    delay_ms: float = 12.0
    loop_gain: float = 1.06
    resonance_freq: float = 2350.0
    resonance_q: float = 32.0
    resonance_enabled: bool = True
    clip_drive: float = 0.35


class _VariableBandPass:
    """Constant 0 dB peak-gain RBJ band-pass with updatable coefficients."""

    def __init__(self, f0: float, q: float, fs: float):
        self.fs = float(fs)
        self._x = np.zeros(2)
        self._y = np.zeros(2)
        self.b = np.ones(3)
        self.a = np.array([1.0, 0.0, 0.0])
        self.set_resonance(f0, q)

    def set_resonance(self, f0: float, q: float) -> None:
        f0 = min(max(f0, 5.0), self.fs / 2.0 - 5.0)
        w0 = 2.0 * math.pi * f0 / self.fs
        alpha = math.sin(w0) / (2.0 * max(q, 0.1))
        cos_w0 = math.cos(w0)
        a0 = 1.0 + alpha
        self.b = np.array([alpha, 0.0, -alpha]) / a0
        self.a = np.array([a0, -2.0 * cos_w0, 1.0 - alpha]) / a0

    def process_block(self, block: np.ndarray) -> np.ndarray:
        out = np.empty_like(block, dtype=float)
        b, a = self.b, self.a
        for n, x in enumerate(block):
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
            out[n] = y
        return out


class FeedbackPlant:
    """Acoustic feedback path: gain -> fractional delay -> room resonance."""

    def __init__(self, config: PlantConfig):
        self.cfg = config
        self.fs = float(config.fs)
        self._delay_line = np.zeros(int(self.fs) + 2)
        self._write = 0
        self._resonator = _VariableBandPass(
            config.resonance_freq, config.resonance_q, self.fs
        )
        self.set_delay(config.delay_ms)

    def set_delay(self, delay_ms: float) -> None:
        self.cfg.delay_ms = float(delay_ms)
        self._delay_frac = max(0.0, delay_ms * 1e-3 * self.fs)

    def set_gain(self, gain: float) -> None:
        self.cfg.loop_gain = float(gain)

    def set_resonance(self, f0: float, q: float | None = None) -> None:
        self.cfg.resonance_freq = float(f0)
        if q is not None:
            self.cfg.resonance_q = float(q)
        self._resonator.set_resonance(self.cfg.resonance_freq, self.cfg.resonance_q)

    def _read_delay(self) -> float:
        d_int = int(math.floor(self._delay_frac))
        frac = self._delay_frac - d_int
        length = self._delay_line.shape[0]
        # _write points at the next empty slot; newest sample is at _write-1.
        # A D-sample delay reads slots _write-1-D and _write-2-D, with linear
        # interpolation implementing the fractional part.
        i0 = (self._write - 1 - d_int) % length
        i1 = (i0 - 1) % length
        return (1.0 - frac) * self._delay_line[i0] + frac * self._delay_line[i1]

    def feedback_block(self, speaker: np.ndarray) -> np.ndarray:
        """Return the acoustic signal fed back into the microphone."""
        delayed = np.empty_like(speaker, dtype=float)
        length = self._delay_line.shape[0]
        for n, sample in enumerate(speaker):
            delayed[n] = self._read_delay()
            self._delay_line[self._write] = float(sample)
            self._write = (self._write + 1) % length

        if self.cfg.resonance_enabled:
            resonant = self._resonator.process_block(delayed)
        else:
            resonant = delayed
        return self.cfg.loop_gain * resonant

    def soft_clip(self, signal_block: np.ndarray) -> np.ndarray:
        """Forward-path limiter that keeps an unstable loop numerically bounded."""
        drive = max(self.cfg.clip_drive, 1e-3)
        return drive * np.tanh(signal_block / drive)

    def feedback_response(self, freqs: np.ndarray) -> np.ndarray:
        """Complex open-loop feedback response G * H_delay * H_resonance."""
        w = 2.0 * math.pi * freqs / self.fs
        z = np.exp(1j * w)
        delay_term = z ** (-self._delay_frac)
        b, a = self._resonator.b, self._resonator.a
        resonance_term = (
            b[0] + b[1] / z + b[2] / z**2
        ) / (a[0] + a[1] / z + a[2] / z**2)
        if not self.cfg.resonance_enabled:
            resonance_term = np.ones_like(freqs, dtype=complex)
        return self.cfg.loop_gain * delay_term * resonance_term
