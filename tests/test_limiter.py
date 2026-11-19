import numpy as np
import pytest

from limiter import LimiterConfig, PeakLimiter
from limiter.analysis import (
    detect_hits,
    hit_peak_levels,
    relative_level_spread,
)
from limiter.signals import drum_pulse_train, sustained_bass


@pytest.fixture
def sr():
    return 48_000.0


@pytest.fixture
def limiter(sr):
    return PeakLimiter(LimiterConfig(sample_rate=sr, ceiling_db=-1.0))


def test_bypass_is_unity_and_zero_latency(limiter, sr):
    x = drum_pulse_train(sr)
    r = limiter.process(x, "bypass")
    np.testing.assert_array_equal(r.output, x)
    assert r.latency_ms == 0.0
    assert r.latency_samples == 0
    assert r.clipping_samples == 0


def test_output_length_preserved_in_all_modes(limiter, sr):
    x = drum_pulse_train(sr)
    for mode in ("bypass", "fixed", "auto"):
        r = limiter.process(x, mode)
        assert len(r.output) == len(x)
        assert len(r.gain) == len(x)


def test_ceiling_respected_with_tight_tolerance(limiter, sr):
    for sig in (drum_pulse_train(sr), sustained_bass(sr)):
        for mode in ("fixed", "auto"):
            r = limiter.process(sig, mode)
            # sample-level guarantee
            assert np.max(np.abs(r.output)) <= limiter.cfg.ceiling_linear * (
                1 + 1e-6
            )
            # true-peak error within 0.05 dB of ceiling
            assert r.max_output_peak_db <= limiter.cfg.ceiling_db + 0.05


def test_latency_matches_lookahead(limiter, sr):
    cfg = limiter.cfg
    expected = int(round(cfg.lookahead_ms / 1000 * sr))
    assert limiter.latency_samples == expected
    assert limiter.latency_ms == pytest.approx(cfg.lookahead_ms, abs=0.01)


def test_relative_hit_dynamics_preserved(limiter, sr):
    x = drum_pulse_train(sr)
    onsets = detect_hits(x, sr)
    in_spread = relative_level_spread(hit_peak_levels(x, onsets, sr))["spread_db"]
    r = limiter.process(x, "auto")
    out_spread = relative_level_spread(
        hit_peak_levels(r.output, onsets, sr)
    )["spread_db"]
    # at least 90% of the original inter-hit spread survives
    assert out_spread >= 0.9 * in_spread


def test_auto_release_within_configured_range(limiter, sr):
    x = sustained_bass(sr)
    r = limiter.process(x, "auto")
    tau = limiter._auto_release_tau(r.raw_gain)
    assert tau.min() >= limiter.cfg.auto_release_min_ms - 1e-9
    assert tau.max() <= limiter.cfg.auto_release_max_ms + 1e-6


def test_short_term_clip_counts(limiter, sr):
    x = drum_pulse_train(sr)
    r = limiter.process(x, "auto")
    counts = limiter.short_term_clip_counts(r, window_ms=50.0)
    assert counts.sum() == r.clipping_samples
    assert len(counts) == int(np.ceil(len(x) / (0.05 * sr)))


def test_invalid_mode_raises(limiter, sr):
    with pytest.raises(ValueError):
        limiter.process(drum_pulse_train(sr), "nonsense")
