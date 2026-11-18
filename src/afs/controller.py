"""Maps confirmed howl detections to a bounded bank of notch filters."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .biquad import NotchBank
from .detector import Detection


@dataclass
class ControllerConfig:
    max_notches: int = 6
    q: float = 18.0
    notch_depth_db: float = 18.0
    retune_bw_fraction: float = 6.0
    min_separation_hz: float = 60.0
    release_idle_blocks: int = 240
    confidence_threshold: float = 0.35


@dataclass
class ControllerEvent:
    kind: str
    slot: int
    freq: float
    q: float


class NotchController:
    def __init__(self, config: ControllerConfig, fs: float):
        self.cfg = config
        self.fs = float(fs)
        self.bank = NotchBank(config.max_notches, fs)
        self._idle: dict[int, int] = {}
        self.events: list[ControllerEvent] = []

    def _notch_bandwidth(self, f0: float) -> float:
        return f0 / self.cfg.q

    def _assign(self, detection: Detection) -> None:
        nearest_idx, nearest_gap = self.bank.nearest_index(detection.freq)
        if nearest_idx >= 0:
            slot_state = self.bank.slots()[nearest_idx]
            bandwidth = self._notch_bandwidth(slot_state.f0)
            # Follow a drifting howl as long as the candidate stays within a
            # few notch bandwidths, even when it leaves the -3 dB notch band.
            retune_limit = max(
                self.cfg.retune_bw_fraction * bandwidth, 300.0
            )
            if nearest_gap <= retune_limit:
                # Same howl drifting slowly: migrate the existing notch.
                self.bank.set_notch(
                    nearest_idx, detection.freq, self.cfg.q, self.cfg.notch_depth_db
                )
                self._idle.pop(nearest_idx, None)
                self.events.append(
                    ControllerEvent("retune", nearest_idx, detection.freq, self.cfg.q)
                )
                return

        # Prefer reusing the slot that has been idle the longest so a
        # re-emerging howl reclaims its old notch before opening new ones.
        target = self.bank.free_index()
        if self._idle:
            target = max(self._idle, key=lambda idx: self._idle[idx])
        if target is None:
            return  # Hard slot limit: extra howls must wait for a free slot.
        self.bank.set_notch(
            target, detection.freq, self.cfg.q, self.cfg.notch_depth_db
        )
        self._idle.pop(target, None)
        self.events.append(
            ControllerEvent("open", target, detection.freq, self.cfg.q)
        )

    def _release_stale(self, active_freqs: set[float]) -> None:
        for idx, state in enumerate(self.bank.slots()):
            if not state.active:
                continue
            still_needed = any(
                abs(state.f0 - freq) <= self.cfg.min_separation_hz
                for freq in active_freqs
            )
            if not still_needed:
                self._idle[idx] = self._idle.get(idx, 0) + 1
                if self._idle[idx] >= self.cfg.release_idle_blocks:
                    self.events.append(
                        ControllerEvent("release", idx, state.f0, state.q)
                    )
                    self.bank.release(idx)
                    del self._idle[idx]
            else:
                self._idle.pop(idx, None)

    def update(self, detections: list[Detection]) -> list[ControllerEvent]:
        self.events = []
        accepted = [
            det
            for det in detections
            if det.confidence >= self.cfg.confidence_threshold
        ]
        for detection in accepted:
            self._assign(detection)
        self._release_stale({det.freq for det in accepted})
        self.bank.age()
        return self.events

    def process_block(self, block: "np.ndarray") -> "np.ndarray":  # pragma: no cover
        return self.bank.process_block(block)

    def reset(self) -> None:
        for idx in range(self.cfg.max_notches):
            self.bank.release(idx)
        self._idle.clear()
        self.events.clear()
