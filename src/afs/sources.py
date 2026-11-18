"""Synthetic stimulus generators for the closed-loop tests."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class SourceConfig:
    fs: float = 16000.0
    duration_s: float = 8.0
    tone_freq: float = 880.0
    tone_start_s: float = 1.0
    tone_stop_s: float = 7.0
    tone_amplitude: float = 0.12
    burst_start_s: float = 3.0
    burst_duration_s: float = 0.12
    noise_floor: float = 0.004
    seed: int = 7


class SourceGenerator:
    """Generates the desired source signal ``s(n)`` block by block.

    The acoustic howl itself is not generated here; it grows from the
    feedback loop. This generator only provides the program material that
    excites the loop (music tone, transient noise, ambient floor).
    """

    def __init__(self, config: SourceConfig):
        self.cfg = config
        self.fs = float(config.fs)
        self._rng = np.random.default_rng(config.seed)
        self._cursor = 0

    def reset(self, seed: int | None = None) -> None:
        self._cursor = 0
        self._rng = np.random.default_rng(
            self.cfg.seed if seed is None else seed
        )

    def render_full(self) -> np.ndarray:
        n = int(round(self.cfg.duration_s * self.fs))
        t = np.arange(n) / self.fs
        signal_block = np.zeros(n)
        mask = (t >= self.cfg.tone_start_s) & (t < self.cfg.tone_stop_s)
        signal_block[mask] = self.cfg.tone_amplitude * np.sin(
            2.0 * np.pi * self.cfg.tone_freq * t[mask]
        )
        b0 = int(self.cfg.burst_start_s * self.fs)
        b1 = b0 + int(self.cfg.burst_duration_s * self.fs)
        b1 = min(b1, n)
        if b1 > b0:
            burst = self._rng.normal(0.0, 1.0, b1 - b0)
            env = np.hanning(b1 - b0)
            signal_block[b0:b1] += 0.5 * env * burst
        signal_block += self.cfg.noise_floor * self._rng.normal(0.0, 1.0, n)
        return signal_block

    def next_block(self, block_size: int) -> np.ndarray:
        full = self.render_full()
        start = self._cursor
        stop = min(start + block_size, full.shape[0])
        block = full[start:stop]
        self._cursor = stop
        if block.shape[0] < block_size:
            block = np.pad(block, (0, block_size - block.shape[0]))
        return block

    @property
    def finished(self) -> bool:
        return self._cursor >= int(round(self.cfg.duration_s * self.fs))
