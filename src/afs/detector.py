"""Frame-based acoustic-howling detector.

A howl is a narrow spectral peak that is simultaneously:

* persistent (keeps reappearing on consecutive frames),
* locally prominent (rises above the surrounding spectrum floor),
* spectrally narrow (energy concentrated at one peak bin),
* coherent in frequency (the peak tracks a slowly drifting tone).

These rules reject short transients (burst noise fails persistence) and
steady musical tones can be kept out by gating on level growth, since a
feedback howl self-amplifies whereas a music tone stays at constant level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np


@dataclass
class DetectorConfig:
    nfft: int = 1024
    hop: int = 512
    min_freq: float = 150.0
    max_freq: float = 7500.0
    peak_prominence_db: float = 12.0
    peak_abs_db: float = -55.0
    narrowness_min: float = 0.28
    persist_frames: int = 6
    drift_tol_hz: float = 140.0
    track_hold_hz: float = 320.0
    growth_db: float = 6.0
    growth_gate: bool = True
    growth_baseline_frac: float = 0.7
    top_k: int = 4
    sustain_level_db: float = -38.0


@dataclass
class Detection:
    """One confirmed howl candidate emitted by the detector."""

    freq: float
    level_db: float
    prominence_db: float
    narrowness: float
    age_frames: int
    confidence: float


@dataclass
class _Track:
    freq: float
    level_db: float
    prominence_db: float
    narrowness: float
    age: int = 1
    seen: int = 1
    min_level_db: float = field(init=False)
    level_history: list = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.min_level_db = self.level_db
        self.level_history = [self.level_db]


class HowlDetector:
    def __init__(self, config: DetectorConfig, fs: float):
        self.cfg = config
        self.fs = float(fs)
        self._buffer = np.zeros(config.nfft)
        self._window = np.hanning(config.nfft)
        self._freqs = np.fft.rfftfreq(config.nfft, d=1.0 / self.fs)
        in_band = (self._freqs >= config.min_freq) & (self._freqs <= config.max_freq)
        self._band_idx = np.flatnonzero(in_band)
        self._frame_index = 0
        self._tracks: list[_Track] = []
        self.last_detections: list[Detection] = []
        self.last_spectrum_db: np.ndarray | None = None

    @property
    def frame_index(self) -> int:
        return self._frame_index

    def _local_floor(self, power: np.ndarray, center: int) -> float:
        half = max(4, self.cfg.nfft // 32)
        lo = max(self._band_idx[0], center - half)
        hi = min(self._band_idx[-1], center + half)
        neighborhood = np.concatenate((power[lo:center], power[center + 1 : hi + 1]))
        neighborhood = neighborhood[neighborhood > 1e-15]
        if neighborhood.size == 0:
            return 1e-15
        # Median floor is robust to neighbouring narrow peaks.
        return float(np.median(neighborhood))

    @staticmethod
    def _narrowness(power: np.ndarray, center: int) -> float:
        """Fraction of local energy concentrated at the peak bin and its
        immediate neighbours; close to 1 for a narrow tone, low for noise."""
        half = max(4, 1024 // 32)
        lo = max(1, center - half)
        hi = center + half + 1
        local_energy = float(np.sum(power[lo:hi])) + 1e-15
        core_energy = float(
            power[center] + power[center - 1] + power[center + 1]
        )
        return core_energy / local_energy

    def _peaks(self, power: np.ndarray) -> list[tuple[int, float, float, float]]:
        candidates: list[tuple[int, float, float, float]] = []
        for idx in self._band_idx:
            if power[idx] <= power[idx - 1] or power[idx] < power[idx + 1]:
                continue
            level_db = 10.0 * math.log10(max(power[idx], 1e-15))
            if level_db < self.cfg.peak_abs_db:
                continue
            floor = self._local_floor(power, idx)
            prominence_db = level_db - 10.0 * math.log10(max(floor, 1e-15))
            if prominence_db < self.cfg.peak_prominence_db:
                continue
            narrowness = self._narrowness(power, idx)
            if narrowness < self.cfg.narrowness_min:
                continue
            candidates.append((idx, level_db, prominence_db, narrowness))
        candidates.sort(key=lambda item: item[1], reverse=True)
        return candidates[: self.cfg.top_k]

    def _update_tracks(
        self, peaks: list[tuple[int, float, float, float]]
    ) -> None:
        matched: set[int] = set()
        for idx, level_db, prominence_db, narrowness in peaks:
            freq = float(self._freqs[idx])
            nearest = -1
            nearest_gap = math.inf
            for track_index, track in enumerate(self._tracks):
                if track_index in matched:
                    continue
                gap = abs(track.freq - freq)
                if gap < nearest_gap:
                    nearest_gap, nearest = gap, track_index
            if nearest >= 0 and nearest_gap <= self.cfg.drift_tol_hz:
                track = self._tracks[nearest]
                # Smooth the centre so it stays stable yet follows a drift.
                track.freq = 0.6 * track.freq + 0.4 * freq
                track.level_db = level_db
                track.prominence_db = prominence_db
                track.narrowness = narrowness
                track.age += 1
                track.seen += 1
                track.min_level_db = min(track.min_level_db, level_db)
                track.level_history.append(level_db)
                if len(track.level_history) > 64:
                    track.level_history.pop(0)
                matched.add(nearest)
            else:
                self._tracks.append(
                    _Track(freq, level_db, prominence_db, narrowness)
                )
                matched.add(len(self._tracks) - 1)
        # Decay unmatched tracks rather than deleting immediately so brief
        # frame-to-frame dropouts do not reset persistence.
        for track_index in reversed(range(len(self._tracks))):
            if track_index not in matched:
                self._tracks[track_index].age -= 1
                if self._tracks[track_index].age <= 0:
                    del self._tracks[track_index]

    def _confirm(self, notch_freqs: np.ndarray | None = None) -> list[Detection]:
        confirmed: list[Detection] = []
        notch_freqs = notch_freqs if notch_freqs is not None else np.array([])
        for track in self._tracks:
            if track.age < self.cfg.persist_frames:
                continue
            near_notch = bool(
                np.any(np.abs(notch_freqs - track.freq) <= self.cfg.track_hold_hz)
            )
            # Tracking/hold cue: a persistent narrow tone close to an
            # existing notch is always confirmed so the controller can
            # migrate the notch as the howl drifts, even while the notch has
            # already driven the level down. This never opens new slots.
            if near_notch and track.prominence_db >= self.cfg.peak_prominence_db:
                pass
            elif self.cfg.growth_gate:
                history = np.asarray(track.level_history)
                if history.size >= self.cfg.persist_frames:
                    baseline = float(
                        np.quantile(
                            history[: max(2, int(history.size * 0.6))],
                            self.cfg.growth_baseline_frac,
                        )
                    )
                    growth = track.level_db - baseline
                else:
                    growth = 0.0
                if growth < self.cfg.growth_db:
                    continue
            else:
                pass
            persistence_score = min(1.0, track.age / (2.0 * self.cfg.persist_frames))
            prominence_score = min(
                1.0, track.prominence_db / (2.0 * self.cfg.peak_prominence_db)
            )
            narrowness_score = min(1.0, track.narrowness / self.cfg.narrowness_min)
            confidence = np.clip(
                0.5 * persistence_score
                + 0.3 * prominence_score
                + 0.2 * narrowness_score,
                0.0,
                1.0,
            )
            confirmed.append(
                Detection(
                    freq=track.freq,
                    level_db=track.level_db,
                    prominence_db=track.prominence_db,
                    narrowness=track.narrowness,
                    age_frames=track.age,
                    confidence=float(confidence),
                )
            )
        confirmed.sort(key=lambda det: det.confidence, reverse=True)
        return confirmed

    def process_block(
        self, block: np.ndarray, notch_freqs: np.ndarray | None = None
    ) -> list[Detection]:
        """Feed one hop-sized time block; returns detections available now."""
        if block.shape[0] != self.cfg.hop:
            raise ValueError(
                f"block length {block.shape[0]} != detector hop {self.cfg.hop}"
            )
        n = self.cfg.nfft
        self._buffer[: n - self.cfg.hop] = self._buffer[self.cfg.hop :]
        self._buffer[n - self.cfg.hop :] = block
        self._frame_index += 1

        windowed = self._buffer * self._window
        spectrum = np.fft.rfft(windowed)
        power = (np.abs(spectrum) ** 2) / max(np.sum(self._window**2), 1e-15)
        self.last_spectrum_db = 10.0 * np.log10(power + 1e-15)

        peaks = self._peaks(power)
        self._update_tracks(peaks)
        self.last_detections = self._confirm(notch_freqs)
        return self.last_detections

    def reset(self) -> None:
        self._buffer.fill(0.0)
        self._tracks.clear()
        self._frame_index = 0
        self.last_detections = []
        self.last_spectrum_db = None
