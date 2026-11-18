from __future__ import annotations

import numpy as np
import pytest

from varispeed import SpeedSchedule, StretchConfig, stretch, stretch_with_info
from varispeed.metrics import dominant_frequency, make_test_signals


@pytest.fixture(scope="module")
def signals():
    return make_test_signals(44_100, 1.5)


@pytest.mark.parametrize("speed", [0.5, 1.0, 2.0])
def test_tone_pitch_and_exact_sample_count(signals, speed):
    config = StretchConfig(sample_rate=44_100, frame_size=2_048)
    result = stretch_with_info(signals.tone, speed, config)
    output = result.audio
    expected = int(round(signals.tone.size / speed))
    assert output.size == expected
    assert result.info.no_ola_holes
    assert result.info.window_gain_cv < 0.01
    measured = dominant_frequency(output, 44_100, fmin=80.0, fmax=500.0)
    assert measured == pytest.approx(220.0, abs=0.02)
    assert np.sqrt(np.mean(output**2)) == pytest.approx(0.6 / np.sqrt(2.0), rel=0.02)


@pytest.mark.parametrize("speed", [0.5, 1.0, 2.0])
def test_sweep_tracks_source_time(signals, speed):
    config = StretchConfig(sample_rate=44_100, frame_size=2_048)
    output = stretch(signals.sweep, speed, config)
    duration = signals.tone.size / 44_100
    slope = (signals.sweep_end - signals.sweep_start) / duration
    window = int(0.04 * 44_100)
    for fraction in (0.2, 0.5, 0.8):
        center = int(round(fraction * duration * 44_100 / speed))
        measured = dominant_frequency(
            output[center - window // 2 : center + window // 2],
            44_100,
            fmin=60.0,
            fmax=2_000.0,
        )
        expected = signals.sweep_start + slope * fraction * duration
        assert measured == pytest.approx(expected, abs=3.0)


@pytest.mark.parametrize(
    ("speed", "max_width_ms"),
    [(0.5, 38.0), (1.0, 0.5), (2.0, 22.0)],
)
def test_pulse_timing_and_diffusion(signals, speed, max_width_ms):
    from varispeed.metrics import pulse_metrics

    config = StretchConfig(sample_rate=44_100, frame_size=2_048)
    output = stretch(signals.pulse, speed, config)
    metrics = pulse_metrics(output, 44_100, signals.pulse_time / speed)
    assert abs(metrics["timing_error_ms"]) < 10.0
    assert metrics["energy_width_90_ms"] < max_width_ms


def test_fast_step_schedule_has_no_gaps(signals):
    schedule = SpeedSchedule.from_pairs(
        [(0.0, 0.5), (0.2, 2.0), (0.4, 0.5), (0.6, 2.0)],
        interpolation="step",
    )
    result = stretch_with_info(
        signals.tone,
        schedule,
        StretchConfig(sample_rate=44_100, frame_size=2_048),
        output_length=22_050,
    )
    assert result.audio.shape == (22_050,)
    assert np.isfinite(result.audio).all()
    assert result.info.no_ola_holes
    assert np.min(np.abs(result.audio)) >= 0.0
    increments = np.diff(np.asarray(result.info.analysis_positions))
    middle = increments[8:-8]
    assert np.all(middle >= 0)
    assert np.max(middle) <= 2 * result.info.synthesis_hop + 1


@pytest.mark.parametrize("speed", [0.49, 2.01, np.nan])
def test_speed_outside_range_is_rejected(signals, speed):
    with pytest.raises(ValueError):
        stretch(signals.tone, speed)


def test_speed_schedule_integration():
    schedule = SpeedSchedule.from_pairs(
        [(0.0, 0.5), (1.0, 2.0), (2.0, 2.0)], interpolation="linear"
    )
    assert schedule.source_position(0.0) == 0.0
    assert schedule.source_position(1.0) == pytest.approx(1.25)
    assert schedule.source_position(2.0) == pytest.approx(3.25)
    delayed = SpeedSchedule.from_pairs([(1.0, 0.5), (2.0, 1.0)])
    assert delayed.source_position(0.5) == pytest.approx(0.25)
    assert delayed.source_position(1.5) == pytest.approx(0.8125)
