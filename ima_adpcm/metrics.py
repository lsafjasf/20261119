"""Objective error measurements for codec demonstrations and tests."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence


@dataclass(frozen=True)
class ErrorMetrics:
    sample_count: int
    rmse: float
    mean_absolute_error: float
    max_absolute_error: int
    snr_db: float

    def as_row(self) -> str:
        snr = "inf" if math.isinf(self.snr_db) else f"{self.snr_db:8.2f}"
        return (
            f"{self.sample_count:6d} {self.rmse:10.2f} "
            f"{self.mean_absolute_error:10.2f} "
            f"{self.max_absolute_error:8d} {snr:>8}"
        )


def compare_samples(
    reference: Sequence[int],
    decoded: Sequence[int],
) -> ErrorMetrics:
    """Compare two equally sized signed PCM sequences."""

    if len(reference) != len(decoded):
        raise ValueError(
            f"sample count mismatch: reference={len(reference)}, decoded={len(decoded)}"
        )
    if not reference:
        return ErrorMetrics(0, 0.0, 0.0, 0, math.inf)
    errors = [int(a) - int(b) for a, b in zip(reference, decoded)]
    squared = sum(error * error for error in errors)
    absolute = sum(abs(error) for error in errors)
    reference_energy = sum(int(value) * int(value) for value in reference)
    error_energy = squared
    if error_energy == 0:
        snr = math.inf
    elif reference_energy == 0:
        snr = -math.inf
    else:
        snr = 10.0 * math.log10(reference_energy / error_energy)
    return ErrorMetrics(
        sample_count=len(reference),
        rmse=math.sqrt(squared / len(reference)),
        mean_absolute_error=absolute / len(reference),
        max_absolute_error=max(abs(error) for error in errors),
        snr_db=snr,
    )
