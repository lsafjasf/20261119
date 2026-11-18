"""Realtime-style block renderer with continuous speed control."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .core import (
    StretchConfig,
    _as_samples,
    _phase_lock,
    _principal_argument,
)


@dataclass
class ControlEvent:
    output_sample: int
    requested_speed: float
    response_start_sample: int
    response_complete_sample: int

    @property
    def latency_samples(self) -> int:
        return self.response_complete_sample - self.output_sample

    def to_dict(self, sample_rate: int) -> dict[str, Any]:
        return {
            "output_sample": self.output_sample,
            "output_time_s": self.output_sample / sample_rate,
            "requested_speed": self.requested_speed,
            "response_start_s": self.response_start_sample / sample_rate,
            "response_complete_s": self.response_complete_sample / sample_rate,
            "latency_samples": self.latency_samples,
            "latency_ms": 1000.0 * self.latency_samples / sample_rate,
        }


class RealtimeStretcher:
    """Pull-based realtime playback engine over a prebuffered source.

    The source is treated as available to the audio callback, as it is for
    a file or memory player. Frames are still synthesized incrementally and
    speed can be changed at any output sample. The fixed synthesis hop
    guarantees that no callback ever observes an OLA gap.
    """

    def __init__(
        self,
        source: np.ndarray,
        speed: float = 1.0,
        config: StretchConfig | None = None,
        *,
        control_ramp_ms: float = 20.0,
    ) -> None:
        self.config = config or StretchConfig()
        samples, was_mono = _as_samples(source)
        self._mono = was_mono
        self._source = samples
        self._channels = samples.shape[1]
        self._hop = self.config.synthesis_hop
        self._frame_size = self.config.frame_size
        self._half = self._frame_size // 2
        self._window = self.config.window
        self._omega = (
            2.0 * np.pi * np.arange(self._frame_size // 2 + 1) / self._frame_size
        )
        self._pad = 3 * self._frame_size
        self._padded = np.pad(samples, ((self._pad, self._pad), (0, 0)))

        self._target_speed = self.config.check_speed(speed)
        self._effective_speed = self._target_speed
        self._ramp_start_speed = self._effective_speed
        self.control_ramp_ms = float(control_ramp_ms)
        if self.control_ramp_ms < 0:
            raise ValueError("control_ramp_ms must be non-negative")
        self._ramp_samples = int(
            round(self.control_ramp_ms * self.config.sample_rate / 1000.0)
        )
        self._ramp_remaining = 0

        self._pre_frames = int(np.ceil(self._frame_size / self._hop)) + 1
        self._next_frame = -self._pre_frames
        self._source_center = self._effective_speed * self._next_frame * self._hop
        self._out_position = 0
        self._buffer_offset = self._pre_frames * self._hop + 2 * self._frame_size
        max_output = int(
            np.ceil(samples.shape[0] / self.config.min_speed)
            + 4 * self._frame_size
        )
        size = max_output + 2 * self._buffer_offset
        self._accum = np.zeros((size, self._channels), dtype=np.float64)
        self._gain = np.zeros(size, dtype=np.float64)

        self._previous_analysis_phase: np.ndarray | None = None
        self._synthesis_phase = np.zeros(
            (self._channels, self._frame_size // 2 + 1)
        )
        self._previous_start: int | None = None
        self._generated_end = 0
        self._underruns = 0
        self.control_events: list[ControlEvent] = []
        self.effective_speeds: list[float] = []

    @property
    def algorithmic_lookahead_samples(self) -> int:
        """Input lookahead required to synthesize one complete frame."""
        return self._frame_size + self._hop

    @property
    def algorithmic_lookahead_ms(self) -> float:
        return (
            1000.0
            * self.algorithmic_lookahead_samples
            / self.config.sample_rate
        )

    @property
    def underruns(self) -> int:
        return self._underruns

    def set_speed(self, speed: float) -> ControlEvent:
        """Change playback speed without resetting frame or phase state."""
        value = self.config.check_speed(speed)
        request_sample = self._out_position
        frames_ahead = max(0, self._next_frame * self._hop - request_sample)
        start = request_sample + frames_ahead
        complete = start + self._ramp_samples
        event = ControlEvent(
            output_sample=request_sample,
            requested_speed=value,
            response_start_sample=start,
            response_complete_sample=complete,
        )
        self.control_events.append(event)
        self._target_speed = value
        self._ramp_start_speed = self._effective_speed
        self._ramp_remaining = self._ramp_samples
        if self._ramp_remaining == 0:
            self._effective_speed = value
        return event

    def _advance_control(self) -> None:
        if self._ramp_remaining <= 0:
            self._effective_speed = self._target_speed
            return
        elapsed = self._ramp_samples - self._ramp_remaining
        fraction = min(1.0, (elapsed + self._hop) / self._ramp_samples)
        self._effective_speed = self._ramp_start_speed + (
            self._target_speed - self._ramp_start_speed
        ) * fraction
        self._ramp_remaining = max(0, self._ramp_remaining - self._hop)

    def _generate_frame(self) -> None:
        center = self._next_frame * self._hop
        start = int(np.round(self._source_center - self._half))
        frame = self._padded[
            start + self._pad : start + self._pad + self._frame_size
        ]
        if frame.shape[0] < self._frame_size:
            frame = np.pad(frame, ((0, self._frame_size - frame.shape[0]), (0, 0)))

        analysis_hop = 0 if self._previous_start is None else start - self._previous_start
        self._previous_start = start
        spectrum = np.fft.rfft(frame * self._window[:, None], axis=0)
        magnitude = np.abs(spectrum)
        analysis_phase = np.angle(spectrum)
        if self._previous_analysis_phase is None:
            self._synthesis_phase = analysis_phase.copy()
        elif analysis_hop != 0:
            deviation = _principal_argument(
                analysis_phase
                - self._previous_analysis_phase
                - self._omega[:, None] * analysis_hop
            )
            self._synthesis_phase = self._synthesis_phase + (
                self._omega[:, None] + deviation / analysis_hop
            ) * self._hop
        self._previous_analysis_phase = analysis_phase.copy()
        if self.config.phase_locking:
            self._synthesis_phase = _phase_lock(
                self._synthesis_phase, analysis_phase, magnitude
            )

        synthesis = np.fft.irfft(
            magnitude * np.exp(1j * self._synthesis_phase),
            n=self._frame_size,
            axis=0,
        ) * self._window[:, None]
        out_start = center - self._half
        lo = out_start + self._buffer_offset
        hi = lo + self._frame_size
        src_lo = max(0, -lo)
        src_hi = min(self._frame_size, self._accum.shape[0] - lo)
        if src_hi > src_lo:
            self._accum[lo + src_lo : lo + src_hi] += synthesis[src_lo:src_hi]
            self._gain[lo + src_lo : lo + src_hi] += (
                self._window[src_lo:src_hi] ** 2
            )

        self._generated_end = max(self._generated_end, out_start + self._frame_size)
        self.effective_speeds.append(self._effective_speed)
        self._next_frame += 1
        self._advance_control()
        self._source_center += self._effective_speed * self._hop

    def _render_until(self, output_end: int) -> None:
        needed_center = output_end + self._half + self._hop
        while self._next_frame * self._hop <= needed_center:
            self._generate_frame()

    def pull(self, sample_count: int) -> np.ndarray:
        """Return exactly ``sample_count`` output samples."""
        if sample_count < 0:
            raise ValueError("sample_count must be non-negative")
        if sample_count == 0:
            return np.zeros((0, self._channels))
        start = self._out_position
        end = start + sample_count
        self._render_until(end)
        gain = self._gain[
            self._buffer_offset + start : self._buffer_offset + end
        ]
        block = self._accum[
            self._buffer_offset + start : self._buffer_offset + end
        ].copy()
        good = gain > 1e-10
        self._underruns += int(np.count_nonzero(~good))
        block[good] /= gain[good, None]
        block[~good] = 0.0
        self._out_position = end
        return block[:, 0] if self._mono else block

    def render(self, output_samples: int) -> tuple[np.ndarray, dict[str, Any]]:
        """Render a fixed number of samples and return stream diagnostics."""
        block_count = max(1, int(np.ceil(output_samples / self._hop)))
        blocks = [
            self.pull(min(self._hop, output_samples - index * self._hop))
            for index in range(block_count)
        ]
        blocks = [block for block in blocks if block.shape[0]]
        audio = np.concatenate(blocks, axis=0)
        info = {
            "algorithmic_lookahead_samples": self.algorithmic_lookahead_samples,
            "algorithmic_lookahead_ms": self.algorithmic_lookahead_ms,
            "control_ramp_ms": self.control_ramp_ms,
            "underruns": self.underruns,
            "effective_speed_min": float(min(self.effective_speeds)),
            "effective_speed_max": float(max(self.effective_speeds)),
            "control_events": [
                event.to_dict(self.config.sample_rate)
                for event in self.control_events
            ],
        }
        return audio, info

    @property
    def effective_speed_track(self) -> tuple[np.ndarray, np.ndarray]:
        """Frame-center times and the effective speed used for each frame."""
        centers = (
            np.arange(len(self.effective_speeds), dtype=np.float64)
            - self._pre_frames
        ) * self._hop
        times = centers / self.config.sample_rate
        speeds = np.asarray(self.effective_speeds, dtype=np.float64)
        return times, speeds
