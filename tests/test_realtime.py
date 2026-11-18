from __future__ import annotations

import numpy as np
import pytest

from varispeed import StretchConfig, stretch, stretch_with_info
from varispeed.metrics import make_test_signals
from varispeed.realtime import RealtimeStretcher
from varispeed.schedule import SpeedSchedule


@pytest.mark.parametrize("speed", [0.5, 1.0, 2.0])
def test_realtime_constant_speed_matches_offline(speed):
    config = StretchConfig(sample_rate=44_100, frame_size=2_048)
    source = make_test_signals(44_100, 1.0).tone
    expected = stretch(source, speed, config)
    engine = RealtimeStretcher(
        source, speed, config, control_ramp_ms=0.0
    )
    actual, info = engine.render(expected.size)
    assert actual.size == expected.size
    assert info["underruns"] == 0
    np.testing.assert_allclose(actual, expected, atol=1e-12)


def test_realtime_switch_is_gapless_and_has_measurable_latency():
    config = StretchConfig(sample_rate=44_100, frame_size=2_048)
    source = make_test_signals(44_100, 2.5).tone
    engine = RealtimeStretcher(
        source, 0.5, config, control_ramp_ms=20.0
    )
    blocks: list[np.ndarray] = []
    position = 0
    switch_at = 22_050
    event = None
    while position < 66_150:
        if event is None and position >= switch_at:
            event = engine.set_speed(2.0)
        count = min(512, 66_150 - position)
        blocks.append(engine.pull(count))
        position += count
    output = np.concatenate(blocks)
    assert event is not None
    assert engine.underruns == 0
    assert np.isfinite(output).all()
    assert np.max(np.abs(output)) > 0.3
    assert all(float(np.max(np.abs(block))) > 0.0 for block in blocks)
    assert event.latency_samples > config.synthesis_hop
    assert event.latency_samples == pytest.approx(
        engine.algorithmic_lookahead_samples + 882, abs=1_100
    )


def test_offline_and_realtime_transition_semantics_differ():
    config = StretchConfig(sample_rate=44_100, frame_size=2_048)
    source = make_test_signals(44_100, 1.5).tone
    length = 44_100
    schedule = SpeedSchedule.from_pairs(
        [(0.0, 0.5), (0.5, 2.0)], interpolation="step"
    )
    offline = stretch_with_info(
        source, schedule, config, output_length=length
    )
    assert offline.audio.size == length
    assert offline.info.no_ola_holes
    assert (
        1000.0 * config.synthesis_hop / config.sample_rate
        < 12.0
    )

    engine = RealtimeStretcher(source, 0.5, config, control_ramp_ms=20.0)
    chunks = [engine.pull(512) for _ in range(10)]
    event = engine.set_speed(2.0)
    remaining = length - sum(chunk.size for chunk in chunks)
    while remaining:
        count = min(512, remaining)
        chunks.append(engine.pull(count))
        remaining -= count
    realtime = np.concatenate(chunks)
    assert realtime.size == length
    assert engine.underruns == 0
    assert event.latency_samples / config.sample_rate > 0.04
    assert np.all(np.isfinite(realtime))
