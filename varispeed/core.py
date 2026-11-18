"""Phase-vocoder time stretching with a fixed, gapless synthesis grid."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from .schedule import MAX_SPEED, MIN_SPEED, SpeedCurve, SpeedSchedule, validate_speed


def _principal_argument(angle: np.ndarray) -> np.ndarray:
    return (angle + np.pi) % (2.0 * np.pi) - np.pi


def _phase_lock(
    synthesis_phase: np.ndarray,
    analysis_phase: np.ndarray,
    magnitude: np.ndarray,
) -> np.ndarray:
    """Lock each FFT bin to the nearest prominent spectral peak.

    Temporal propagation happens on the peak. Neighbouring bins keep their
    within-frame phase relationship to that peak, which prevents the
    vertical phase incoherence that hollows out sustained tones.
    """
    bins = magnitude.shape[0]
    if bins < 3:
        return synthesis_phase
    locked = synthesis_phase.copy()
    for channel in range(magnitude.shape[1]):
        mag = magnitude[:, channel]
        candidates = np.flatnonzero(
            (mag[1:-1] > mag[:-2])
            & (mag[1:-1] >= mag[2:])
            & (mag[1:-1] > max(float(np.max(mag)) * 1e-4, 1e-12))
        ) + 1
        if candidates.size == 0:
            continue
        if candidates.size > 1 and mag[0] > mag[candidates[0]]:
            candidates = np.concatenate(([0], candidates))
        if candidates.size > 1 and mag[-1] > mag[candidates[-1]]:
            candidates = np.concatenate((candidates, [bins - 1]))
        boundaries = (candidates[:-1] + candidates[1:] + 1) // 2
        starts = np.concatenate(([0], boundaries))
        stops = np.concatenate((boundaries, [bins]))
        for peak, lo, hi in zip(candidates, starts, stops):
            relative = analysis_phase[lo:hi, channel] - analysis_phase[peak, channel]
            locked[lo:hi, channel] = synthesis_phase[peak, channel] + relative
    return locked


@dataclass(frozen=True)
class StretchConfig:
    """Numerical settings for the phase vocoder.

    The synthesis hop is deliberately independent of speed. Every speed
    therefore writes frames onto the same OLA grid; changing speed only
    changes how far the next analysis frame advances.
    """

    sample_rate: int = 44_100
    frame_size: int = 2_048
    hop_ratio: float = 0.25
    min_speed: float = MIN_SPEED
    max_speed: float = MAX_SPEED
    phase_locking: bool = True
    transient_reset: bool = False

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.frame_size < 16:
            raise ValueError("frame_size must be at least 16")
        if not 0.0 < self.hop_ratio <= 0.5:
            raise ValueError("hop_ratio must be in (0, 0.5]")
        if not 0.0 < self.min_speed <= self.max_speed:
            raise ValueError("invalid speed limits")

    @property
    def synthesis_hop(self) -> int:
        return max(1, int(round(self.frame_size * self.hop_ratio)))

    @property
    def window(self) -> np.ndarray:
        # sqrt-Hann analysis * sqrt-Hann synthesis gives a smooth COLA pair.
        return np.sqrt(np.hanning(self.frame_size))

    def check_speed(self, speed: float) -> float:
        value = validate_speed(speed)
        if value < self.min_speed or value > self.max_speed:
            raise ValueError(
                f"speed {value:g} is outside configured range "
                f"[{self.min_speed:g}, {self.max_speed:g}]"
            )
        return value


@dataclass
class StretchInfo:
    sample_rate: int
    input_samples: int
    output_samples: int
    frame_size: int
    synthesis_hop: int
    frame_count: int
    speed_min: float
    speed_max: float
    effective_speed_min: float
    effective_speed_max: float
    window_gain_min: float
    window_gain_max: float
    window_gain_cv: float
    no_ola_holes: bool
    phase_step_max: float
    analysis_positions: list[int] = field(default_factory=list)
    synthesis_centers: list[int] = field(default_factory=list)

    def to_dict(self, *, include_tracks: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_tracks:
            data.pop("analysis_positions")
            data.pop("synthesis_centers")
        return data


@dataclass
class StretchResult:
    audio: np.ndarray
    info: StretchInfo


def _as_samples(audio: np.ndarray) -> tuple[np.ndarray, bool]:
    samples = np.asarray(audio, dtype=np.float64)
    if samples.ndim == 1:
        return samples[:, None], True
    if samples.ndim != 2:
        raise ValueError("audio must be mono or shaped (samples, channels)")
    if samples.shape[0] == 0:
        raise ValueError("audio must not be empty")
    return samples, False


def _duration_samples(
    sample_count: int,
    sample_rate: int,
    curve: SpeedCurve,
    output_length: int | None,
) -> int:
    if output_length is not None:
        if output_length <= 0:
            raise ValueError("output_length must be positive")
        return int(output_length)
    seconds = curve.duration_for_source(sample_count, sample_rate)
    return max(1, int(round(seconds * sample_rate)))


def stretch_with_info(
    audio: np.ndarray,
    speed: float | SpeedSchedule = 1.0,
    config: StretchConfig | None = None,
    *,
    output_length: int | None = None,
    times: np.ndarray | None = None,
    interpolation: str = "linear",
) -> StretchResult:
    """Time-stretch ``audio`` while keeping its pitch.

    ``speed`` is playback speed: 2.0 makes the result half as long and
    0.5 makes it twice as long. A :class:`SpeedSchedule` (or ``speed`` as a
    sequence together with ``times``) changes speed on the output timeline.
    """
    cfg = config or StretchConfig()
    samples, was_mono = _as_samples(audio)
    n_in, channels = samples.shape
    if n_in < 2:
        raise ValueError("audio needs at least two samples")

    if times is not None:
        curve = SpeedCurve(speed, times=times, interpolation=interpolation)
    elif isinstance(speed, SpeedSchedule):
        curve = SpeedCurve(speed)
    else:
        curve = SpeedCurve(cfg.check_speed(float(speed)))
    schedule_speeds = np.asarray(curve.schedule.speeds, dtype=np.float64)
    if np.any(schedule_speeds < cfg.min_speed) or np.any(
        schedule_speeds > cfg.max_speed
    ):
        raise ValueError("speed schedule leaves the configured range")

    n_out = _duration_samples(n_in, cfg.sample_rate, curve, output_length)
    hop = cfg.synthesis_hop
    frame_size = cfg.frame_size
    window = cfg.window
    pad_in = 3 * frame_size
    padded = np.pad(samples, ((pad_in, pad_in), (0, 0)))

    # Frames to the left of zero are needed to fill the beginning of the
    # first output window. They read the zero-padded prehistory.
    pre_frames = int(np.ceil(frame_size / hop)) + 1
    post_frames = int(np.ceil((n_out + frame_size) / hop)) + pre_frames + 2
    first_frame = -pre_frames
    last_frame = post_frames
    buf_pad = frame_size * 2
    accum = np.zeros((n_out + 2 * buf_pad, channels), dtype=np.float64)
    gain = np.zeros(n_out + 2 * buf_pad, dtype=np.float64)

    half = frame_size // 2
    omega = 2.0 * np.pi * np.arange(frame_size // 2 + 1) / frame_size
    previous_analysis_phase: np.ndarray | None = None
    previous_magnitude: np.ndarray | None = None
    synthesis_phase = np.zeros((channels, frame_size // 2 + 1))
    previous_start: int | None = None
    phase_step_max = 0.0

    positions: list[int] = []
    centers: list[int] = []
    effective_speeds: list[float] = []
    frame_count = 0

    for frame_index in range(first_frame, last_frame + 1):
        center = frame_index * hop
        source_center = curve.source_position(center / cfg.sample_rate) * cfg.sample_rate
        start = int(np.round(source_center - half))
        centers.append(center)
        positions.append(start)

        frame = padded[start + pad_in : start + pad_in + frame_size]
        if frame.shape[0] < frame_size:
            frame = np.pad(frame, ((0, frame_size - frame.shape[0]), (0, 0)))
        frame_count += 1

        analysis_hop = 0 if previous_start is None else start - previous_start
        if previous_start is not None:
            effective_speeds.append(abs(analysis_hop) / hop)
        previous_start = start

        spectrum = np.fft.rfft(frame * window[:, None], axis=0)
        magnitude = np.abs(spectrum)
        analysis_phase = np.angle(spectrum)

        if previous_analysis_phase is None:
            synthesis_phase = analysis_phase.copy()
        elif analysis_hop != 0:
            expected = omega[:, None] * analysis_hop
            deviation = _principal_argument(
                analysis_phase - previous_analysis_phase - expected
            )
            instantaneous = omega[:, None] + deviation / analysis_hop
            step = instantaneous * hop
            phase_step_max = max(
                phase_step_max,
                float(np.max(np.abs(_principal_argument(step))))
                if step.size
                else 0.0,
            )
            synthesis_phase = synthesis_phase + step
        previous_analysis_phase = analysis_phase.copy()

        if cfg.transient_reset and previous_magnitude is not None:
            previous_energy = float(np.sum(previous_magnitude**2))
            current_energy = float(np.sum(magnitude**2))
            flux = float(np.sum(np.maximum(magnitude - previous_magnitude, 0.0)))
            if current_energy > 4.0 * previous_energy + 1e-12 and flux > 1e-8:
                synthesis_phase = analysis_phase.copy()
        previous_magnitude = magnitude.copy()

        if cfg.phase_locking:
            synthesis_phase = _phase_lock(
                synthesis_phase, analysis_phase, magnitude
            )
        synthesis = np.fft.irfft(
            magnitude * np.exp(1j * synthesis_phase), n=frame_size, axis=0
        )
        synthesis *= window[:, None]
        out_start = center - half
        lo = out_start + buf_pad
        hi = lo + frame_size
        if hi <= 0 or lo >= accum.shape[0]:
            continue
        src_lo = max(0, -lo)
        src_hi = min(frame_size, accum.shape[0] - lo)
        dst_lo = lo + src_lo
        dst_hi = lo + src_hi
        accum[dst_lo:dst_hi] += synthesis[src_lo:src_hi]
        gain[dst_lo:dst_hi] += window[src_lo:src_hi] ** 2

    valid_gain = gain[buf_pad : buf_pad + n_out]
    floor = 1e-10
    if float(np.min(valid_gain)) <= floor:
        raise RuntimeError("OLA gain vanished; synthesis grid has a hole")
    output = accum[buf_pad : buf_pad + n_out] / valid_gain[:, None]

    active_speeds = np.asarray(effective_speeds, dtype=np.float64)
    nonzero_speeds = active_speeds[active_speeds > 1e-9]
    if nonzero_speeds.size == 0:
        raise RuntimeError("no analysis advance was generated")
    gain_mean = float(np.mean(valid_gain))
    info = StretchInfo(
        sample_rate=cfg.sample_rate,
        input_samples=n_in,
        output_samples=n_out,
        frame_size=frame_size,
        synthesis_hop=hop,
        frame_count=frame_count,
        speed_min=float(np.min(schedule_speeds)),
        speed_max=float(np.max(schedule_speeds)),
        effective_speed_min=float(np.min(nonzero_speeds)),
        effective_speed_max=float(np.max(nonzero_speeds)),
        window_gain_min=float(np.min(valid_gain)),
        window_gain_max=float(np.max(valid_gain)),
        window_gain_cv=float(np.std(valid_gain) / gain_mean),
        no_ola_holes=bool(np.all(valid_gain > floor)),
        phase_step_max=phase_step_max,
        analysis_positions=positions,
        synthesis_centers=centers,
    )
    result = output[:, 0] if was_mono else output
    return StretchResult(audio=result, info=info)


def stretch(
    audio: np.ndarray,
    speed: float | SpeedSchedule = 1.0,
    config: StretchConfig | None = None,
    *,
    output_length: int | None = None,
    times: np.ndarray | None = None,
    interpolation: str = "linear",
) -> np.ndarray:
    """Return only the stretched samples."""
    return stretch_with_info(
        audio,
        speed,
        config,
        output_length=output_length,
        times=times,
        interpolation=interpolation,
    ).audio


stretch_offline = stretch
