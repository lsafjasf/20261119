"""Dual-instance crossfaded filter switching.

When the impulse response is replaced, the old convolver keeps running while
a fresh convolver for the new filter is brought up in parallel.  Their
outputs are mixed over a finite transition region, after which the old
instance is released.  Both instances are fed the *same* input stream, so
the switch does not disturb streaming block boundaries.
"""

from __future__ import annotations

import math


def linear_gain(n: int) -> tuple[list[float], list[float]]:
    """Equal-power-free linear fade: old goes 1->0, new goes 0->1."""
    if n <= 0:
        return [], []
    old = [1.0 - k / (n - 1) if n > 1 else 0.0 for k in range(n)]
    new = [k / (n - 1) if n > 1 else 1.0 for k in range(n)]
    return old, new


def equal_power_gain(n: int) -> tuple[list[float], list[float]]:
    """Cosine equal-power fade (gain squares sum to 1)."""
    if n <= 0:
        return [], []
    theta = [0.5 * math.pi * k / (n - 1) if n > 1 else 0.5 * math.pi
             for k in range(n)]
    old = [math.cos(t) for t in theta]
    new = [math.sin(t) for t in theta]
    return old, new


class CrossfadeConvolver:
    """Wrap any streaming convolver with glitch-free filter replacement."""

    def __init__(self, factory, impulse: list[float],
                 fade_samples: int, gain: str = "linear"):
        self._factory = factory
        self.fade_samples = fade_samples
        self.old = factory(impulse)
        self.new = None
        self.impulse = list(impulse)
        self._gain = gain
        self.extra_mac_count = 0
        self.extra_memory_peak = 0
        self._output: list[float] = []
        self._history: list[float] = []
        self.switch_sample: int | None = None
        self._samples_fed = 0
        self._fading = False
        self._flushed = False

    def _gains(self, n: int):
        if self._gain == "equal_power":
            return equal_power_gain(n)
        return linear_gain(n)

    def switch(self, new_impulse: list[float]) -> None:
        """Replace the filter and begin a crossfade at the current output.

        The new convolver is primed by replaying the accumulated input
        history so both instances carry identical filter state for the same
        input position; only their impulse responses differ.
        """
        if self._fading:
            raise RuntimeError("previous crossfade still in progress")
        self.new = self._factory(new_impulse)
        self.impulse = list(new_impulse)
        self.new.process(self._history)  # prime, discard its past output
        self.switch_sample = len(self._output)
        self._fading = True
        if hasattr(self.old, "memory_slots") and hasattr(self.new, "memory_slots"):
            self.extra_memory_peak = self.new.memory_slots()["total_real_equiv"]

    def _mix(self, old_y: list[float], new_y: list[float]) -> list[float]:
        n = len(old_y)
        f = self.fade_samples
        s = self.switch_sample or 0
        out = [0.0] * n
        old_g, new_g = self._gains(f)
        for i in range(n):
            global_idx = len(self._output) + i
            k = global_idx - s
            if k < 0:
                out[i] = old_y[i]
            elif k < f:
                out[i] = old_g[k] * old_y[i] + new_g[k] * new_y[i]
            else:
                out[i] = new_y[i]
        if self._fading and len(self._output) + n >= s + f:
            self._fading = False
            self.old = self.new
            self.new = None
        return out

    def process(self, signal: list[float]) -> list[float]:
        self._samples_fed += len(signal)
        self._history.extend(signal)
        old_y = self.old.process(signal)
        if self.new is None:
            self._output.extend(old_y)
            return old_y
        new_y = self.new.process(signal)
        mixed = self._mix(old_y, new_y)
        self._output.extend(mixed)
        self.extra_mac_count += getattr(self.new, "complex_mac_count", 0)
        return mixed

    def flush(self) -> list[float]:
        if self._flushed:
            return []
        self._flushed = True
        old_y = self.old.flush()
        if self.new is None:
            self._output.extend(old_y)
            return old_y
        new_y = self.new.flush()
        mixed = self._mix(old_y, new_y)
        self._output.extend(mixed)
        return mixed

    @property
    def output(self) -> list[float]:
        return list(self._output)

    def run(self, signal: list[float]) -> list[float]:
        """Full streaming run; returns the complete crossfaded output."""
        self.process(signal)
        self.flush()
        # Child convolvers flush out to a block boundary; the linear
        # convolution itself is exactly N + M - 1 samples long.
        length = self._samples_fed + len(self.impulse) - 1
        return self._output[:length]

    def transition_region_error(
        self,
        old_full: list[float],
        new_full: list[float],
    ) -> dict[str, float]:
        """Compare against the ideal gain-crossfade of the two fixed filters.

        ``old_full`` / ``new_full`` are complete (time-domain or exact
        fast-convolution) outputs produced with each filter held constant.
        Error is evaluated over the fade window plus one hop margin.
        """
        if self.switch_sample is None:
            raise RuntimeError("switch() was never called")
        f = self.fade_samples
        s = self.switch_sample
        old_g, new_g = self._gains(f)
        end = min(len(self._output), s + f + self.block_hint())
        errs = []
        for idx in range(s, end):
            k = idx - s
            if k < f:
                ideal = old_g[k] * old_full[idx] + new_g[k] * new_full[idx]
            else:
                ideal = new_full[idx]
            errs.append(abs(self._output[idx] - ideal))
        if not errs:
            return {"max_abs": 0.0, "rms": 0.0, "samples": 0}
        rms = math.sqrt(sum(e * e for e in errs) / len(errs))
        return {"max_abs": max(errs), "rms": rms, "samples": len(errs)}

    def block_hint(self) -> int:
        return getattr(self.old, "block", 1)
