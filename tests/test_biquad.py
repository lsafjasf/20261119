import math

import numpy as np
import pytest

from afs.biquad import BiquadFilter, NotchBank, design_notch


FS = 16000.0


def test_design_notch_coefficients_and_depth():
    b, a = design_notch(1000.0, 20.0, FS)
    freqs = np.linspace(10.0, FS / 2 - 10.0, 4096)
    w = 2 * math.pi * freqs / FS
    z = np.exp(1j * w)
    h = (b[0] + b[1] / z + b[2] / z**2) / (a[0] + a[1] / z + a[2] / z**2)
    idx = np.argmin(np.abs(freqs - 1000.0))
    assert 20 * np.log10(np.abs(h[idx])) < -40.0
    # Unity gain well away from the notch.
    far = np.abs(freqs - 1000.0) > 400.0
    assert np.allclose(np.abs(h[far]), 1.0, atol=2e-2)


def test_biquad_rejects_invalid_frequency():
    with pytest.raises(ValueError):
        design_notch(FS / 2.0, 10.0, FS)
    with pytest.raises(ValueError):
        design_notch(1000.0, -1.0, FS)


def test_notch_bank_slot_limits_and_cascade():
    bank = NotchBank(max_notches=3, fs=FS)
    assert bank.active_count == 0
    for k, f0 in enumerate((1000.0, 2000.0, 3000.0)):
        idx = bank.free_index()
        assert idx == k
        bank.set_notch(idx, f0, q=18.0, depth_db=18.0)
    assert bank.free_index() is None
    assert bank.active_count == 3

    response = bank.frequency_response(np.array([1000.0, 2000.0, 3000.0, 500.0]))
    assert np.all(20 * np.log10(response[:3] + 1e-12) < -30.0)
    assert response[3] > 0.95

    bank.release(1)
    assert bank.active_count == 2
    assert bank.free_index() == 1


def test_biquad_process_block_matches_scipy_reference():
    from scipy.signal import iirnotch, lfilter

    rng = np.random.default_rng(0)
    block = rng.normal(0.0, 1.0, 2048)
    b_ref, a_ref = iirnotch(1500.0, 18.0, fs=FS)
    expected = lfilter(b_ref, a_ref, block)

    filt = BiquadFilter(FS)
    filt.set_notch(1500.0, 18.0)
    got = filt.process_block(block)
    assert np.allclose(got, expected, atol=1e-6, rtol=1e-6)
