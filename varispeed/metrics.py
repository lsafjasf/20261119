"""Signal generation and objective measurements used by the demo suite."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def rms(signal: np.ndarray) -> float:
    data = np.asarray(signal, dtype=np.float64)
    return float(np.sqrt(np.mean(data * data))) if data.size else 0.0


def peak(signal: np.ndarray) -> float:
    return float(np.max(np.abs(signal))) if np.size(signal) else 0.0


def dominant_frequency(
    signal: np.ndarray,
    sample_rate: int,
    *,
    fmin: float = 20.0,
    fmax: float | None = None,
    zero_pad: int = 1 << 19,
) -> float:
    """Estimate the strongest frequency with a Hann window and parabolic peak."""
    data = np.asarray(signal, dtype=np.float64)
    if data.ndim != 1 or data.size < 8:
        raise ValueError("dominant_frequency requires a mono signal")
    data = data - float(np.mean(data))
    if rms(data) < 1e-12:
        return 0.0
    n_fft = max(int(zero_pad), 1 << int(np.ceil(np.log2(data.size))))
    spectrum = np.abs(np.fft.rfft(data * np.hanning(data.size), n=n_fft))
    freqs = np.arange(spectrum.size) * sample_rate / n_fft
    upper = sample_rate * 0.5 if fmax is None else min(fmax, sample_rate * 0.5)
    valid = np.flatnonzero((freqs >= fmin) & (freqs <= upper))
    if valid.size == 0:
        return 0.0
    index = int(valid[np.argmax(spectrum[valid])])
    if 0 < index < spectrum.size - 1:
        left, center, right = spectrum[index - 1 : index + 2]
        denominator = left - 2.0 * center + right
        offset = (
            0.5 * (left - right) / denominator
            if abs(denominator) > 1e-20
            else 0.0
        )
        offset = float(np.clip(offset, -0.5, 0.5))
    else:
        offset = 0.0
    return float((index + offset) * sample_rate / n_fft)


def spectral_centroid(signal: np.ndarray, sample_rate: int) -> float:
    data = np.asarray(signal, dtype=np.float64)
    if rms(data) < 1e-12:
        return 0.0
    spectrum = np.abs(np.fft.rfft(data * np.hanning(data.size)))
    freqs = np.arange(spectrum.size) * sample_rate / (2.0 * (spectrum.size - 1))
    total = float(np.sum(spectrum))
    return float(np.sum(freqs * spectrum) / total) if total else 0.0


def spectral_flatness(signal: np.ndarray) -> float:
    spectrum = np.abs(np.fft.rfft(np.asarray(signal, dtype=np.float64)))
    spectrum = spectrum[1:]
    power = spectrum * spectrum + 1e-20
    return float(np.exp(np.mean(np.log(power))) / np.mean(power))


def stft_magnitude(
    signal: np.ndarray,
    *,
    frame_size: int = 1_024,
    hop: int = 256,
) -> np.ndarray:
    data = np.asarray(signal, dtype=np.float64)
    if data.size < frame_size:
        data = np.pad(data, (0, frame_size - data.size))
    window = np.hanning(frame_size)
    count = 1 + (data.size - frame_size) // hop
    out = np.empty((frame_size // 2 + 1, count), dtype=np.float64)
    for index in range(count):
        frame = data[index * hop : index * hop + frame_size] * window
        out[:, index] = np.abs(np.fft.rfft(frame))
    return out


def sinusoid_residual(
    signal: np.ndarray,
    sample_rate: int,
    frequency: float,
) -> tuple[np.ndarray, dict[str, float]]:
    """Remove the best-fit sinusoid at ``frequency`` and return the residual."""
    data = np.asarray(signal, dtype=np.float64)
    time = np.arange(data.size) / sample_rate
    basis = np.column_stack(
        (np.sin(2.0 * np.pi * frequency * time), np.cos(2.0 * np.pi * frequency * time))
    )
    coefficients, *_ = np.linalg.lstsq(basis, data, rcond=None)
    fitted = basis @ coefficients
    residual = data - fitted
    signal_power = float(np.sum(data * data))
    residual_power = float(np.sum(residual * residual))
    return residual, {
        "frequency_hz": frequency,
        "residual_rms": rms(residual),
        "signal_rms": rms(data),
        "residual_to_signal_db": 10.0
        * np.log10(max(residual_power, 1e-20) / max(signal_power, 1e-20)),
    }


def pulse_metrics(
    signal: np.ndarray,
    sample_rate: int,
    expected_time_s: float,
    *,
    search_ms: float = 200.0,
) -> dict[str, float | int]:
    """Measure peak location, energy spread, and peak level of one transient."""
    data = np.asarray(signal, dtype=np.float64)
    energy = data * data
    expected = int(round(expected_time_s * sample_rate))
    half_search = int(round(search_ms * sample_rate / 1000.0))
    lo = max(0, expected - half_search)
    hi = min(data.size, expected + half_search)
    if hi <= lo or float(np.sum(energy[lo:hi])) <= 1e-20:
        return {
            "peak_sample": -1,
            "peak_time_ms": -1.0,
            "timing_error_ms": -1.0,
            "energy_width_90_ms": -1.0,
            "energy_width_50_ms": -1.0,
            "peak_amplitude": 0.0,
            "energy": 0.0,
        }
    local_peak = lo + int(np.argmax(energy[lo:hi]))
    segment = energy[lo:hi]
    cumulative = np.cumsum(segment)
    total = float(cumulative[-1])

    def crossing(fraction: float) -> int:
        return lo + int(np.searchsorted(cumulative, fraction * total))

    p05, p50a, p50b, p95 = (
        crossing(0.05),
        crossing(0.25),
        crossing(0.75),
        crossing(0.95),
    )
    return {
        "peak_sample": local_peak,
        "peak_time_ms": 1000.0 * local_peak / sample_rate,
        "timing_error_ms": 1000.0 * (local_peak - expected) / sample_rate,
        "energy_width_90_ms": 1000.0 * (p95 - p05) / sample_rate,
        "energy_width_50_ms": 1000.0 * (p50b - p50a) / sample_rate,
        "peak_amplitude": peak(data[lo:hi]),
        "energy": float(total),
    }


@dataclass(frozen=True)
class TestSignals:
    tone: np.ndarray
    sweep: np.ndarray
    pulse: np.ndarray
    tone_frequency: float = 220.0
    sweep_start: float = 120.0
    sweep_end: float = 1_200.0
    pulse_time: float = 0.75


def make_test_signals(
    sample_rate: int = 44_100,
    duration_s: float = 2.0,
) -> TestSignals:
    count = int(round(sample_rate * duration_s))
    time = np.arange(count) / sample_rate
    fade = np.ones(count)
    fade_len = min(count // 20, int(0.02 * sample_rate))
    ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(fade_len) / fade_len)
    fade[:fade_len] = ramp
    fade[-fade_len:] = ramp[::-1]

    tone = 0.6 * np.sin(2.0 * np.pi * 220.0 * time) * fade
    sweep_start, sweep_end = 120.0, 1_200.0
    slope = (sweep_end - sweep_start) / duration_s
    sweep_phase = 2.0 * np.pi * (sweep_start * time + 0.5 * slope * time**2)
    sweep = 0.55 * np.sin(sweep_phase) * fade

    pulse = np.zeros(count)
    pulse_index = int(round(0.75 * sample_rate))
    pulse[pulse_index] = 1.0
    return TestSignals(tone=tone, sweep=sweep, pulse=pulse)


def make_demo_phrase(sample_rate: int = 44_100) -> np.ndarray:
    """A short harmonic phrase with sharp note onsets for listening tests."""
    duration = 4.0
    count = int(round(sample_rate * duration))
    time = np.arange(count) / sample_rate
    notes = [220.0, 277.18, 329.63, 440.0, 329.63, 277.18, 220.0, 440.0]
    note_len = duration / len(notes)
    output = np.zeros(count)
    for index, frequency in enumerate(notes):
        start = int(round(index * note_len * sample_rate))
        stop = min(count, int(round((index + 1) * note_len * sample_rate)))
        local_t = time[start:stop] - time[start]
        local_n = stop - start
        attack_n = max(1, int(0.006 * sample_rate))
        release_n = max(1, int(0.05 * sample_rate))
        envelope = np.ones(local_n)
        attack = np.linspace(0.0, 1.0, attack_n, endpoint=False)
        release = np.linspace(1.0, 0.0, release_n)
        envelope[: min(attack_n, local_n)] = attack[: min(attack_n, local_n)]
        envelope[-min(release_n, local_n) :] = release[-min(release_n, local_n) :]
        voice = np.zeros(local_n)
        for harmonic in range(1, 7):
            if frequency * harmonic >= sample_rate * 0.45:
                break
            voice += (
                np.sin(2.0 * np.pi * frequency * harmonic * local_t + 0.17 * harmonic)
                / harmonic**1.25
            )
        output[start:stop] = 0.30 * voice * envelope
        click_at = start + int(0.002 * sample_rate)
        if click_at < count:
            output[click_at] += 0.22
        if index % 2 == 1:
            click_at = stop - int(0.008 * sample_rate)
            if 0 <= click_at < count:
                output[click_at] -= 0.15
    return output
