"""Validation suite for the event camera simulator.

Run:  python3 validate_simulator.py
"""

import os
import tempfile

import numpy as np

from event_camera import (
    EventCameraSimulator,
    load_events,
    save_events,
    time_surface,
)

W, H = 64, 48
DARK, BRIGHT = 0.1, 0.9


def moving_edge_frames(v=50.0, fps=100.0, x0=8.0, x1=56.0,
                       dark=DARK, bright=BRIGHT, direction="dark2bright"):
    """Vertical edge sweeping right at speed v (px/s)."""
    n = int((x1 - x0) / v * fps) + 1
    ts = np.arange(n + 1) / fps
    frames = []
    for t in ts:
        edge = x0 + v * t
        left = np.arange(W) < edge
        if direction == "dark2bright":
            img = np.full((H, W), dark)
            img[:, left] = bright
        else:
            img = np.full((H, W), bright)
            img[:, left] = dark
        frames.append(img)
    return frames, ts


def test_moving_edge_timing_and_polarity():
    frames, ts = moving_edge_frames(direction="dark2bright")
    sim = EventCameraSimulator(W, H, C_pos=0.2, C_off=0.2)
    events = sim.run(frames, ts)
    assert events.size > 0, "no events from moving edge"
    assert np.all(events["p"] == 1), "dark->bright sweep must emit ON only"

    # Mean event time per column must match the edge arrival time.
    x0, v = 8.0, 50.0
    errs = []
    for x in range(12, 53):
        col = events[events["x"] == x]
        assert col.size > 0, f"column {x} produced no events"
        t_expected = (x + 0.5 - x0) / v
        errs.append(abs(col["t"].mean() - t_expected))
    max_err = max(errs)
    assert max_err < 1.5 / v, f"edge timing off by {max_err:.4f}s"

    # Times must increase monotonically with column index (constant speed).
    means = [events[events["x"] == x]["t"].mean() for x in range(12, 53)]
    assert np.all(np.diff(means) > 0), "edge not moving at constant speed"

    # Opposite sweep: bright -> dark must emit OFF only.
    frames, ts = moving_edge_frames(direction="bright2dark")
    sim = EventCameraSimulator(W, H, C_pos=0.2, C_off=0.2)
    events = sim.run(frames, ts)
    assert events.size > 0
    assert np.all(events["p"] == -1), "bright->dark sweep must emit OFF only"
    print(f"PASS moving edge: timing max err {max_err*1e3:.2f} ms, "
          f"polarities correct")


def test_static_scene_silent():
    img = np.random.default_rng(1).random((H, W)) * 0.8 + 0.1
    ts = np.arange(200) / 100.0
    sim = EventCameraSimulator(W, H)
    events = sim.run([img] * len(ts), ts)
    assert events.size == 0, f"static scene emitted {events.size} events"

    # One change, then static again: events only near the change.
    sim = EventCameraSimulator(W, H)
    frames = [img] * 10 + [np.clip(img * 1.5, 0, 1)] + \
             [np.clip(img * 1.5, 0, 1)] * 50
    ts = np.arange(len(frames)) / 100.0
    events = sim.run(frames, ts)
    assert events.size > 0
    assert events["t"].max() < ts[12], "static tail keeps emitting events"
    print("PASS static scene: silent, no trailing events")


def test_high_contrast_multiple_triggers():
    sim = EventCameraSimulator(W, H, C_pos=0.2, C_off=0.2)
    sim.process_frame(np.full((H, W), DARK), 0.0)
    ev = sim.process_frame(np.full((H, W), BRIGHT), 0.01)
    n_expected = int(np.floor(np.log(BRIGHT / DARK) / 0.2))
    per_pixel = np.zeros((H, W), dtype=int)
    np.add.at(per_pixel, (ev["y"], ev["x"]), 1)
    assert np.all(per_pixel == n_expected), (
        f"expected {n_expected} events/pixel, got {per_pixel.min()}.."
        f"{per_pixel.max()}")
    assert np.all(ev["p"] == 1)
    assert ev["t"].min() > 0.0 and ev["t"].max() <= 0.01
    # Timestamps at one pixel must be strictly increasing.
    px = ev[(ev["x"] == 30) & (ev["y"] == 20)]
    assert np.all(np.diff(px["t"]) > 0)
    print(f"PASS high contrast: {n_expected} triggers/pixel in one interval")


def test_refractory_period():
    refr = 0.003
    sim = EventCameraSimulator(W, H, C_pos=0.2, refractory_period=refr)
    sim.process_frame(np.full((H, W), DARK), 0.0)
    ev = sim.process_frame(np.full((H, W), BRIGHT), 0.01)
    px = ev[(ev["x"] == 30) & (ev["y"] == 20)]
    assert px.size >= 2
    gaps = np.diff(px["t"])
    assert np.all(gaps >= refr - 1e-9), f"refractory violated: {gaps.min()}"
    n_no_refr = int(np.floor(np.log(BRIGHT / DARK) / 0.2))
    assert px.size < n_no_refr, "refractory did not drop any event"
    print(f"PASS refractory: {px.size}/{n_no_refr} events kept, "
          f"min gap {gaps.min()*1e3:.2f} ms")


def test_hot_pixels():
    img = np.full((H, W), 0.5)
    ts = np.arange(100) / 100.0
    sim = EventCameraSimulator(W, H, hot_pixel_rate=200.0,
                               hot_pixel_fraction=0.02, seed=3)
    events = sim.run([img] * len(ts), ts)
    assert events.size > 0, "hot pixels produced nothing"
    flat = events["y"].astype(int) * W + events["x"].astype(int)
    assert set(np.unique(flat)) <= set(sim.hot_pixels.tolist()), \
        "spurious event outside hot pixel set"
    expected = 200.0 * ts[-1] * len(sim.hot_pixels)
    assert 0.5 * expected < events.size < 1.6 * expected, (
        f"hot pixel count {events.size} vs expected ~{expected:.0f}")
    print(f"PASS hot pixels: {events.size} events from "
          f"{len(sim.hot_pixels)} hot pixels")


def test_threshold_mismatch():
    sim = EventCameraSimulator(W, H, C_pos=0.2, threshold_sigma=0.3, seed=5)
    sim.process_frame(np.full((H, W), DARK), 0.0)
    ev = sim.process_frame(np.full((H, W), BRIGHT), 0.01)
    per_pixel = np.zeros((H, W), dtype=int)
    np.add.at(per_pixel, (ev["y"], ev["x"]), 1)
    expected = np.floor(np.log(BRIGHT / DARK) / sim.th_pos).astype(int)
    assert np.array_equal(per_pixel, expected), \
        "per-pixel counts do not match per-pixel thresholds"
    assert len(np.unique(per_pixel)) > 3, "mismatch had no visible effect"
    print(f"PASS threshold mismatch: counts vary "
          f"{per_pixel.min()}..{per_pixel.max()} per pixel")


def test_event_file_roundtrip():
    frames, ts = moving_edge_frames()
    sim = EventCameraSimulator(W, H)
    events = sim.run(frames, ts)
    with tempfile.TemporaryDirectory() as d:
        for name in ("ev.npz", "ev.txt"):
            path = os.path.join(d, name)
            save_events(path, events)
            back = load_events(path)
            assert back.shape == events.shape
            assert np.allclose(back["t"], events["t"], atol=1e-9)
            assert np.array_equal(back["x"], events["x"])
            assert np.array_equal(back["y"], events["y"])
            assert np.array_equal(back["p"], events["p"])
    print("PASS event file round-trip (.npz and .txt)")


def test_time_surface():
    frames, ts = moving_edge_frames()
    sim = EventCameraSimulator(W, H)
    events = sim.run(frames, ts)
    t_now = float(events["t"].max())
    surf = time_surface(events, W, H, t_now, tau=0.03)
    assert surf.shape == (H, W)
    assert surf.max() <= 1.0 and surf.min() >= 0.0
    assert surf.max() > 0.9, "time surface has no recent activity"
    print("PASS time surface: bounded, peaks at recent events")


if __name__ == "__main__":
    test_moving_edge_timing_and_polarity()
    test_static_scene_silent()
    test_high_contrast_multiple_triggers()
    test_refractory_period()
    test_hot_pixels()
    test_threshold_mismatch()
    test_event_file_roundtrip()
    test_time_surface()
    print("\nAll validation tests passed.")
