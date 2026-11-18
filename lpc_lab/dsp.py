"""Core linear-prediction analysis in pure Python.

The predictor convention used throughout the project is

    x[n] ~= sum(a[i] * x[n-i], i=1..order)

so the prediction-error filter polynomial is

    A(z) = 1 - a[1] z^-1 - ... - a[order] z^-order.

All DSP routines deliberately avoid NumPy/SciPy so the encoder and the
generated report can run in a stock Python installation.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, log10, pi, sin, sqrt
from typing import Sequence

SILENCE_ENERGY = 1e-20
STABILITY_EPS = 1e-9


@dataclass(frozen=True)
class AnalysisConfig:
    """Parameters shared by analysis and synthesis."""

    sample_rate: int
    frame_length: int = 400
    hop_length: int = 400
    window_length: int = 320
    order: int = 12
    window_type: str = "hann"
    preemphasis: float = 0.0

    def validate(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.frame_length <= 0:
            raise ValueError("frame_length must be positive")
        if self.hop_length <= 0:
            raise ValueError("hop_length must be positive")
        if self.hop_length != self.frame_length:
            raise ValueError(
                "this laboratory uses non-overlapping frames; "
                "hop_length must equal frame_length"
            )
        if not 1 <= self.window_length <= self.frame_length:
            raise ValueError("window_length must be in [1, frame_length]")
        if not 1 <= self.order < self.window_length:
            raise ValueError("order must be in [1, window_length)")
        if self.window_type not in {"hann", "rectangular"}:
            raise ValueError("window_type must be 'hann' or 'rectangular'")
        if not 0.0 <= self.preemphasis < 1.0:
            raise ValueError("preemphasis must be in [0, 1)")


def hann_window(length: int) -> list[float]:
    if length <= 1:
        return [1.0] * length
    return [0.5 - 0.5 * cos(2.0 * pi * n / (length - 1)) for n in range(length)]


def _window(frame: Sequence[float], window_length: int, kind: str) -> list[float]:
    offset = (len(frame) - window_length) // 2
    if kind == "rectangular":
        weights = [1.0] * window_length
    else:
        weights = hann_window(window_length)
    result = [0.0] * len(frame)
    for i, weight in enumerate(weights):
        result[offset + i] = float(frame[offset + i]) * weight
    return result


def preemphasize(samples: Sequence[float], coefficient: float) -> list[float]:
    if coefficient <= 0.0:
        return [float(x) for x in samples]
    result = [0.0] * len(samples)
    previous = 0.0
    for n, value in enumerate(samples):
        current = float(value)
        result[n] = current - coefficient * previous
        previous = current
    return result


def deemphasize(samples: Sequence[float], coefficient: float) -> list[float]:
    if coefficient <= 0.0:
        return [float(x) for x in samples]
    result = [0.0] * len(samples)
    previous = 0.0
    for n, value in enumerate(samples):
        current = float(value) + coefficient * previous
        result[n] = current
        previous = current
    return result


def frame_count(sample_count: int, frame_length: int, hop_length: int) -> int:
    if sample_count <= 0:
        return 0
    return (sample_count + hop_length - 1) // hop_length


def get_frame(
    samples: Sequence[float], index: int, frame_length: int, hop_length: int
) -> tuple[list[float], int]:
    start = index * hop_length
    frame = [0.0] * frame_length
    end = min(start + frame_length, len(samples))
    if start < len(samples):
        frame[: end - start] = [float(x) for x in samples[start:end]]
    return frame, start


def autocorrelation(samples: Sequence[float], order: int) -> list[float]:
    """Unbiased-by-lag energy correlations r[k] = sum x[n] x[n-k]."""

    length = len(samples)
    result: list[float] = []
    for lag in range(order + 1):
        total = 0.0
        for n in range(lag, length):
            total += samples[n] * samples[n - lag]
        result.append(total)
    return result


def autocorrelation_matrix(correlations: Sequence[float], order: int) -> list[list[float]]:
    """Return the symmetric Toeplitz matrix R[i][j] = r[abs(i-j)]."""

    return [
        [float(correlations[abs(row - column)]) for column in range(order + 1)]
        for row in range(order + 1)
    ]


def levinson_durbin(
    correlations: Sequence[float], order: int
) -> tuple[list[float], list[float], float, bool, bool, str]:
    """Solve the normal equations.

    Returns (predictor, reflection, error_energy, stable, was_clipped, method).
    Predictor has length order+1 and predictor[0] == 1.
    """

    if order < 1:
        raise ValueError("order must be at least 1")
    if len(correlations) < order + 1:
        raise ValueError("not enough autocorrelation lags")

    r0 = float(correlations[0])
    if not (r0 > SILENCE_ENERGY):
        zeros = [0.0] * (order + 1)
        zeros[0] = 1.0
        return zeros, [0.0] * order, 0.0, True, False, "zero_energy"

    predictor = [0.0] * (order + 1)
    predictor[0] = 1.0
    reflection: list[float] = []
    error = r0
    clipped = False

    for stage in range(1, order + 1):
        if error <= SILENCE_ENERGY:
            reflection.extend([0.0] * (order - stage + 1))
            break
        accumulator = float(correlations[stage])
        for j in range(1, stage):
            accumulator -= predictor[j] * correlations[stage - j]
        k = accumulator / error
        if not (-0.999999 <= k <= 0.999999):
            limit = 0.999999
            k = limit if k > 0.0 else -limit
            clipped = True
        reflection.append(k)
        previous = predictor[:]
        predictor[stage] = k
        for j in range(1, stage):
            predictor[j] = previous[j] - k * previous[stage - j]
        error *= max(1.0 - k * k, 1e-12)

    if len(reflection) < order:
        reflection.extend([0.0] * (order - len(reflection)))

    stable = all(abs(value) < 1.0 for value in reflection)
    method = "levinson_reflection_clipped" if clipped else "levinson"
    return predictor, reflection, max(error, 0.0), stable, clipped, method


def prediction_error(
    samples: Sequence[float],
    predictor: Sequence[float],
    history: Sequence[float] = (),
) -> list[float]:
    """Calculate e[n] = x[n] - sum(a[i]x[n-i]).

    ``history`` contains samples immediately before ``samples`` in reverse
    chronological order (history[0] is x[-1]).
    """

    order = len(predictor) - 1
    result = [0.0] * len(samples)
    for n, value in enumerate(samples):
        prediction = 0.0
        for i in range(1, order + 1):
            index = n - i
            if index >= 0:
                delayed = samples[index]
            elif i - 1 < len(history):
                delayed = history[i - 1]
            else:
                delayed = 0.0
            prediction += predictor[i] * delayed
        result[n] = float(value) - prediction
    return result


def synthesize_frame(
    residual: Sequence[float],
    predictor: Sequence[float],
    history: Sequence[float] = (),
) -> list[float]:
    """All-pole synthesis using reconstructed history."""

    order = len(predictor) - 1
    output = [0.0] * len(residual)
    for n, excitation in enumerate(residual):
        prediction = 0.0
        for i in range(1, order + 1):
            index = n - i
            if index >= 0:
                delayed = output[index]
            elif i - 1 < len(history):
                delayed = history[i - 1]
            else:
                delayed = 0.0
            prediction += predictor[i] * delayed
        output[n] = float(excitation) + prediction
    return output


def _polynomial_value(coefficients: Sequence[complex], value: complex) -> complex:
    result = 0j
    for coefficient in coefficients:
        result = result * value + coefficient
    return result


def polynomial_roots(coefficients: Sequence[float], iterations: int = 150) -> list[complex]:
    """Find roots with Durand-Kerner (adequate for small LPC polynomials)."""

    coefficients = [complex(value) for value in coefficients]
    while len(coefficients) > 1 and abs(coefficients[0]) < 1e-15:
        coefficients.pop(0)
    degree = len(coefficients) - 1
    if degree <= 0:
        return []
    if degree == 1:
        return [-coefficients[1] / coefficients[0]]

    radius = 0.85
    roots = [
        radius * complex(cos(2.0 * pi * i / degree), sin(2.0 * pi * i / degree))
        for i in range(degree)
    ]
    for _ in range(iterations):
        largest_delta = 0.0
        updated = roots[:]
        for i, root in enumerate(roots):
            denominator = 1.0 + 0j
            for j, other in enumerate(roots):
                if i != j:
                    denominator *= root - other
            if abs(denominator) < 1e-30:
                denominator = 1e-30 + 0j
            delta = _polynomial_value(coefficients, root) / denominator
            updated[i] = root - delta
            largest_delta = max(largest_delta, abs(delta))
        roots = updated
        if largest_delta < 1e-13:
            break
    return roots


def predictor_poles(predictor: Sequence[float]) -> list[complex]:
    """Poles of 1/A(z), i.e. roots of z^p-a1 z^(p-1)-...-ap."""

    if len(predictor) <= 1:
        return []
    coefficients = [1.0] + [-float(value) for value in predictor[1:]]
    return polynomial_roots(coefficients)


def pole_radius(predictor: Sequence[float]) -> float:
    poles = predictor_poles(predictor)
    return max((abs(root) for root in poles), default=0.0)


def all_pole_stable(predictor: Sequence[float]) -> bool:
    return pole_radius(predictor) < 1.0 - STABILITY_EPS


def reflection_to_predictor(reflection: Sequence[float]) -> list[float]:
    predictor = [1.0]
    for stage, k in enumerate(reflection, start=1):
        previous = predictor[:]
        predictor.append(k)
        for j in range(1, stage):
            predictor[j] = previous[j] - k * previous[stage - j]
    return predictor


def rms(samples: Sequence[float]) -> float:
    if not samples:
        return 0.0
    return sqrt(sum(value * value for value in samples) / len(samples))


def snr_db(reference: Sequence[float], estimate: Sequence[float]) -> float:
    count = min(len(reference), len(estimate))
    signal_energy = sum(reference[i] * reference[i] for i in range(count))
    noise_energy = sum(
        (reference[i] - estimate[i]) ** 2 for i in range(count)
    )
    if signal_energy <= SILENCE_ENERGY:
        return 0.0
    if noise_energy <= 1e-30:
        return 120.0
    return 10.0 * log10(signal_energy / noise_energy)


def analyze_frame(
    frame: Sequence[float],
    config: AnalysisConfig,
    index: int,
    start: int,
) -> dict:
    """Analyze one frame and return a JSON-serializable record."""

    windowed = _window(frame, config.window_length, config.window_type)
    correlations = autocorrelation(windowed, config.order)
    predictor, reflection, error, stable, clipped, method = levinson_durbin(
        correlations, config.order
    )
    estimate = synthesize_frame(frame, predictor)
    error_signal = [float(frame[n] - estimate[n]) for n in range(len(frame))]
    poles = predictor_poles(predictor)
    radius = max((abs(root) for root in poles), default=0.0)
    excitation_rms = rms(error_signal)
    frame_rms = rms(frame)
    gain_ratio = (
        frame_rms / excitation_rms
        if frame_rms > 1e-30 and excitation_rms > 1e-30
        else 0.0
    )
    frame_energy = sum(value * value for value in frame)
    is_silence = frame_energy <= SILENCE_ENERGY

    return {
        "index": index,
        "start": start,
        "is_silence": is_silence,
        "frame_rms": frame_rms,
        "residual_rms": excitation_rms,
        "prediction_gain_db": (
            20.0 * log10(gain_ratio)
            if gain_ratio > 0.0
            else 0.0
        ),
        "autocorrelation": correlations,
        "autocorrelation_matrix": autocorrelation_matrix(
            correlations, config.order
        ),
        "predictor": predictor,
        "reflection": reflection,
        "prediction_error_energy": error,
        "prediction_error": error_signal,
        "frame_samples": [float(value) for value in frame],
        "poles": [{"real": root.real, "imag": root.imag} for root in poles],
        "pole_radius": radius,
        "stable": stable and radius < 1.0,
        "clipped_during_levinson": clipped,
        "method": method,
    }


def analyze_signal(samples: Sequence[float], config: AnalysisConfig) -> dict:
    """Analyze every frame; residual uses original-signal history."""

    config.validate()
    working = preemphasize(samples, config.preemphasis)
    count = frame_count(len(working), config.frame_length, config.hop_length)
    frames: list[dict] = []
    residual: list[float] = []
    for index in range(count):
        frame, start = get_frame(
            working, index, config.frame_length, config.hop_length
        )
        record = analyze_frame(frame, config, index, start)
        history = list(reversed(working[max(0, start - config.order) : start]))
        frame_residual = prediction_error(frame, record["predictor"], history)
        record["residual"] = frame_residual
        record["prediction_error"] = frame_residual
        residual_rms = rms(frame_residual)
        gain_ratio = (
            record["frame_rms"] / residual_rms
            if record["frame_rms"] > 1e-30 and residual_rms > 1e-30
            else 0.0
        )
        record["residual_rms"] = residual_rms
        record["prediction_gain_db"] = (
            20.0 * log10(gain_ratio)
            if gain_ratio > 0.0
            else 0.0
        )
        frames.append(record)
        residual.extend(frame_residual)

    return {
        "config": {
            "sample_rate": config.sample_rate,
            "frame_length": config.frame_length,
            "hop_length": config.hop_length,
            "window_length": config.window_length,
            "order": config.order,
            "window_type": config.window_type,
            "preemphasis": config.preemphasis,
        },
        "sample_count": len(samples),
        "frame_count": count,
        "frames": frames,
        "residual": residual[: len(samples)],
    }
