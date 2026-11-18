"""Speed schedules and the output-time to source-time mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

MIN_SPEED = 0.5
MAX_SPEED = 2.0


def validate_speed(speed: float) -> float:
    value = float(speed)
    if not np.isfinite(value):
        raise ValueError("speed must be finite")
    if value < MIN_SPEED or value > MAX_SPEED:
        raise ValueError(
            f"speed {value:g} is outside the supported range "
            f"[{MIN_SPEED:g}, {MAX_SPEED:g}]"
        )
    return value


@dataclass(frozen=True)
class SpeedSchedule:
    """Piecewise speed controls indexed by output (playback) time.

    ``interpolation`` is either ``"linear"`` or ``"step"``. Speeds outside
    the control points are clamped to the first/last value. At least one
    point is required and all speeds must be inside [0.5, 2.0].
    """

    times: tuple[float, ...]
    speeds: tuple[float, ...]
    interpolation: str = "linear"

    def __post_init__(self) -> None:
        times = tuple(float(t) for t in self.times)
        speeds = tuple(validate_speed(s) for s in self.speeds)
        if len(times) != len(speeds) or not times:
            raise ValueError("times and speeds must be non-empty and equal length")
        if np.any(np.diff(np.asarray(times)) <= 0):
            raise ValueError("schedule times must be strictly increasing")
        if self.interpolation not in ("linear", "step"):
            raise ValueError("interpolation must be 'linear' or 'step'")
        object.__setattr__(self, "times", times)
        object.__setattr__(self, "speeds", speeds)

    @classmethod
    def from_pairs(
        cls,
        points: Iterable[Sequence[float]],
        *,
        interpolation: str = "linear",
    ) -> "SpeedSchedule":
        pairs = [(float(t), float(s)) for t, s in points]
        if not pairs:
            raise ValueError("at least one control point is required")
        times, speeds = zip(*pairs)
        return cls(tuple(times), tuple(speeds), interpolation)

    @classmethod
    def constant(cls, speed: float) -> "SpeedSchedule":
        return cls((0.0,), (validate_speed(speed),), "step")

    def speed_at(self, time_s: float) -> float:
        t = float(time_s)
        if t <= self.times[0]:
            return self.speeds[0]
        if t >= self.times[-1]:
            return self.speeds[-1]
        index = int(np.searchsorted(self.times, t, side="right") - 1)
        t0, t1 = self.times[index], self.times[index + 1]
        s0, s1 = self.speeds[index], self.speeds[index + 1]
        if self.interpolation == "step":
            return s0
        return s0 + (s1 - s0) * (t - t0) / (t1 - t0)

    def source_position(self, time_s: float) -> float:
        """Integral of speed from output time zero to ``time_s``."""
        t = float(time_s)
        if t == 0:
            return 0.0
        if t < 0:
            return -self.source_position(-t)
        return self._integral_positive(t)

    def _integral_positive(self, t: float) -> float:
        if t <= 0:
            return 0.0
        if t <= self.times[0]:
            return self.speeds[0] * t
        total = self.speeds[0] * max(0.0, self.times[0])
        cursor = max(0.0, self.times[0])
        for index in range(len(self.times) - 1):
            left, right = self.times[index], self.times[index + 1]
            s_left, s_right = self.speeds[index], self.speeds[index + 1]
            if t <= left:
                break
            end = min(t, right)
            if end > left:
                if self.interpolation == "step":
                    total += s_left * (end - left)
                else:
                    span = right - left
                    s_end = s_left + (s_right - s_left) * (end - left) / span
                    total += 0.5 * (s_left + s_end) * (end - left)
            cursor = right
            if t <= right:
                return total
        if t > self.times[-1]:
            total += self.speeds[-1] * (t - max(cursor, self.times[-1]))
        return total


class SpeedCurve:
    """Scalar or scheduled speed presented through one small interface."""

    def __init__(
        self,
        speed: float | SpeedSchedule = 1.0,
        *,
        times: Iterable[float] | None = None,
        interpolation: str = "linear",
    ) -> None:
        if isinstance(speed, SpeedSchedule):
            if times is not None:
                raise ValueError("times cannot be combined with SpeedSchedule")
            schedule = speed
        elif times is not None:
            values = np.atleast_1d(np.asarray(speed, dtype=np.float64))
            time_values = tuple(times)
            if values.size != len(time_values):
                raise ValueError("speed and times must have equal length")
            schedule = SpeedSchedule.from_pairs(
                zip(time_values, values.tolist()), interpolation=interpolation
            )
        else:
            schedule = SpeedSchedule.constant(validate_speed(speed))
        self.schedule = schedule
        self.constant = len(schedule.times) == 1
        self._value = schedule.speeds[0]

    @property
    def value(self) -> float:
        return self._value

    def speed_at(self, time_s: float) -> float:
        return self._value if self.constant else self.schedule.speed_at(time_s)

    def source_position(self, time_s: float) -> float:
        if self.constant:
            return self._value * float(time_s)
        return self.schedule.source_position(time_s)

    def source_positions(self, times: np.ndarray) -> np.ndarray:
        times = np.asarray(times, dtype=np.float64)
        if self.constant:
            return self._value * times
        return np.asarray([self.source_position(t) for t in times], dtype=np.float64)

    def duration_for_source(self, source_samples: int, sample_rate: int) -> float:
        """Smallest output time whose source position reaches the input end."""
        if self.constant:
            return source_samples / (sample_rate * self._value)
        target = source_samples / float(sample_rate)
        last_t = self.schedule.times[-1]
        if self.schedule.source_position(last_t) >= target:
            hi = last_t
        else:
            hi = last_t + (target - self.schedule.source_position(last_t)) / MIN_SPEED
        lo = 0.0
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            if self.schedule.source_position(mid) < target:
                lo = mid
            else:
                hi = mid
        return hi
