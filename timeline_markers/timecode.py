"""Integer time helpers used by the marker model.

All persisted times are integer ticks.  Fractions are used only for transient
rate conversions, and the result of a conversion is rounded once.
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import TypeAlias

TICKS_PER_SECOND = 1_000_000

Number: TypeAlias = int | float | str | Fraction


def as_fraction(value: Number | tuple[int, int] | list[int]) -> Fraction:
    """Return an exact fraction for a rate or duration value.

    Floats are interpreted through their shortest decimal representation, so
    ``0.1`` becomes ``1/10`` rather than a binary floating-point tail.
    """

    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):
        raise TypeError("boolean values are not valid rates or durations")
    if isinstance(value, int):
        return Fraction(value, 1)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("rate and duration values must be finite")
        return Fraction(str(value))
    if isinstance(value, str):
        try:
            return Fraction(value)
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError(f"invalid fraction: {value!r}") from exc
    if isinstance(value, (tuple, list)) and len(value) == 2:
        numerator, denominator = value
        if not isinstance(numerator, int) or not isinstance(denominator, int):
            raise TypeError("fraction pairs must contain integers")
        return Fraction(numerator, denominator)
    raise TypeError(f"unsupported numeric value: {type(value).__name__}")


def round_fraction(value: Fraction) -> int:
    """Round halves away from zero, deterministically."""

    if value >= 0:
        quotient, remainder = divmod(value.numerator, value.denominator)
        return quotient + (1 if remainder * 2 >= value.denominator else 0)
    return -round_fraction(-value)


def seconds_to_ticks(
    seconds: Number | tuple[int, int] | list[int],
    *,
    ticks_per_second: int = TICKS_PER_SECOND,
) -> int:
    if ticks_per_second <= 0:
        raise ValueError("ticks_per_second must be positive")
    return round_fraction(as_fraction(seconds) * ticks_per_second)


def ticks_to_seconds(
    ticks: int,
    *,
    ticks_per_second: int = TICKS_PER_SECOND,
) -> Fraction:
    if ticks_per_second <= 0:
        raise ValueError("ticks_per_second must be positive")
    return Fraction(ticks, ticks_per_second)


def format_timecode(
    ticks: int,
    *,
    ticks_per_second: int = TICKS_PER_SECOND,
) -> str:
    """Format ticks as ``HH:MM:SS.mmm`` for CLI and documentation output."""

    sign = "-" if ticks < 0 else ""
    absolute = abs(ticks)
    total_ms = round_fraction(Fraction(absolute * 1000, ticks_per_second))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
