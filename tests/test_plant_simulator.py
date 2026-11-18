import numpy as np

from afs.controller import ControllerConfig
from afs.detector import DetectorConfig
from afs.plant import FeedbackPlant, PlantConfig
from afs.scenarios import SCENARIOS, build_simulator
from afs.simulator import evaluate


FS = 16000


def test_plant_open_loop_response_peak_at_resonance():
    cfg = PlantConfig(fs=FS, resonance_freq=2300.0, resonance_q=40.0)
    plant = FeedbackPlant(cfg)
    freqs = np.linspace(200.0, 7000.0, 8192)
    magnitude = np.abs(plant.feedback_response(freqs))
    peak_freq = freqs[np.argmax(magnitude)]
    assert abs(peak_freq - 2300.0) < 60.0


def test_unstable_loop_grows_without_suppression():
    scenario = SCENARIOS["drifting_howl"]()
    result = build_simulator(scenario).run(enable_suppression=False)
    from afs.simulator import _band_rms_db

    # The limiter bounds absolute amplitude; compare spectral howl energy at
    # the (drifting) resonance band instead of raw wideband RMS.
    target = scenario.drift.end_hz
    band = (target - 120.0, target + 120.0)
    early_band = _band_rms_db(result.mic[: int(FS)], FS, band)
    late_band = _band_rms_db(result.mic[-int(FS) :], FS, band)
    assert late_band > early_band + 10.0


def test_suppression_reduces_level_and_uses_bounded_notches():
    scenario = SCENARIOS["drifting_howl"]()
    controlled = build_simulator(scenario).run(enable_suppression=True)
    reference = build_simulator(scenario).run(enable_suppression=False)
    metrics = evaluate(controlled, reference, notch_q=scenario.controller.q)

    assert metrics["notches_used"] <= scenario.controller.max_notches
    from afs.simulator import _band_rms_db

    target = controlled.controller.bank.frequencies()
    assert target.size >= 1
    f0 = float(target[-1])
    band = (f0 - f0 / scenario.controller.q, f0 + f0 / scenario.controller.q)
    ctrl_band = _band_rms_db(controlled.mic[-int(FS) :], FS, band)
    ref_band = _band_rms_db(reference.mic[-int(FS) :], FS, band)
    assert ctrl_band < ref_band - 10.0
    assert metrics["remaining_peak_db"] < 0.0
    assert metrics["suppression_latency_s"] is not None
    assert metrics["suppression_latency_s"] < 0.5


def test_stable_tone_and_burst_do_not_open_notches():
    for name in ("stable_music_tone", "burst_noise"):
        scenario = SCENARIOS[name]()
        result = build_simulator(scenario).run(enable_suppression=True)
        assert result.controller.bank.active_count == 0, name
