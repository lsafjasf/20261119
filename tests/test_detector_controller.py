import numpy as np

from afs.controller import ControllerConfig, NotchController
from afs.detector import DetectorConfig, HowlDetector


FS = 16000.0


def _growing_tone(hop, n_frames, freq, start_db=-45.0, growth=1.4):
    blocks = []
    rng = np.random.default_rng(1)
    amp = 10 ** (start_db / 20.0)
    cursor = 0
    for _ in range(n_frames):
        t = (cursor + np.arange(hop)) / FS
        tone = amp * np.sin(2 * np.pi * freq * t)
        blocks.append(tone + 0.001 * rng.normal(size=hop))
        amp *= 10 ** (growth / 20.0)
        cursor += hop
    return blocks


def test_detector_confirms_persistent_growing_howl():
    cfg = DetectorConfig(hop=512, persist_frames=5, growth_db=5.0)
    detector = HowlDetector(cfg, FS)
    blocks = _growing_tone(cfg.hop, n_frames=14, freq=2400.0)
    found = []
    for block in blocks:
        found = detector.process_block(block)
    assert found, "growing persistent tone must be detected as a howl"
    assert any(abs(d.freq - 2400.0) < cfg.nfft / FS * FS * 0.6 for d in found)
    assert all(0.0 <= d.confidence <= 1.0 for d in found)


def test_detector_rejects_short_transient():
    cfg = DetectorConfig(hop=512, persist_frames=6)
    detector = HowlDetector(cfg, FS)
    rng = np.random.default_rng(2)
    detections = []
    for frame in range(12):
        block = 0.002 * rng.normal(size=cfg.hop)
        if frame == 6:
            block += 0.4 * rng.normal(size=cfg.hop)
        detections = detector.process_block(block)
    assert detections == []


def test_controller_respects_slot_limit_and_retunes():
    cfg = ControllerConfig(max_notches=2, retune_bw_fraction=2.0)
    controller = NotchController(cfg, FS)

    from afs.detector import Detection

    d1 = Detection(1000.0, -20.0, 20.0, 0.9, 8, 0.8)
    d2 = Detection(2000.0, -18.0, 22.0, 0.9, 8, 0.8)
    d3 = Detection(3000.0, -19.0, 21.0, 0.9, 8, 0.8)
    controller.update([d1])
    controller.update([d2])
    controller.update([d3])
    assert controller.bank.active_count == 2

    # A frequency close to an existing notch retunes that slot.
    d1_drift = Detection(1040.0, -19.0, 21.0, 0.9, 9, 0.8)
    controller.update([d1_drift, d2])
    freqs = controller.bank.frequencies()
    assert np.any(np.abs(freqs - 1040.0) < 1.0)
    assert controller.bank.active_count == 2


def test_controller_releases_idle_slots():
    cfg = ControllerConfig(max_notches=3, release_idle_blocks=2)
    controller = NotchController(cfg, FS)
    from afs.detector import Detection

    controller.update([Detection(1500.0, -20.0, 20.0, 0.9, 8, 0.8)])
    assert controller.bank.active_count == 1
    for _ in range(3):
        controller.update([])
    assert controller.bank.active_count == 0
