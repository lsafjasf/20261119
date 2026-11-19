from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from portraitcrop.io import load_annotations, load_pins, write_windows_csv
from portraitcrop.metrics import compute_metrics
from portraitcrop.models import (
    AnnotationSet,
    CropSpec,
    FrameAnnotation,
    Region,
    SolveResult,
    Window,
)
from portraitcrop.solver import SolverConfig


class IoMetricsTests(unittest.TestCase):
    def test_loads_absolute_and_normalized_boxes(self) -> None:
        payload = {
            "width": 1000,
            "height": 500,
            "fps": 25,
            "frames": [
                {
                    "frame": 0,
                    "regions": [
                        {"id": "a", "bbox": [10, 20, 100, 200], "confidence": 0.9},
                        {
                            "id": "b",
                            "bbox": [0.5, 0.2, 0.1, 0.4],
                            "score": 0.7,
                            "normalized": True,
                        },
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rois.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            annotations = load_annotations(path)
        self.assertEqual(annotations.frames[0].regions[0].box.width, 100)
        self.assertEqual(annotations.frames[0].regions[1].box.x, 500)
        self.assertAlmostEqual(annotations.frames[0].regions[1].confidence, 0.7)

    def test_pins_accept_time_and_region_ids(self) -> None:
        payload = {
            "pins": [
                {"time": 1.0, "region_id": "a", "align": "center"},
                {"frame": 4, "x": 10, "hard": False},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pins.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            pins = load_pins(path, fps=30)
        by_frame = {pin.frame: pin for pin in pins}
        self.assertEqual(by_frame[30].region_ids, ("a",))
        self.assertFalse(by_frame[4].hard)

    def test_metrics_count_out_of_bounds_and_jitter(self) -> None:
        annotations = AnnotationSet(
            1000,
            500,
            30,
            [FrameAnnotation(index, index / 30, []) for index in range(4)],
        )
        crop = CropSpec(1000, 500, 200, 500, 0)
        result = SolveResult(
            status="ok",
            crop=crop,
            fps=30,
            windows=[
                Window(0, 0.0, -1, 0, 200, 500, 1.0),
                Window(1, 1 / 30, 0, 0, 200, 500, 1.0),
                Window(2, 2 / 30, 10, 0, 200, 500, 1.0),
                Window(3, 3 / 30, 5, 0, 200, 500, 1.0),
            ],
        )
        metrics = compute_metrics(annotations, result, SolverConfig())
        self.assertEqual(metrics["constraints"]["out_of_bounds_frames"], 1)
        self.assertGreater(metrics["motion"]["jitter_rms_px_s2"], 0)

    def test_csv_has_frame_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "windows.csv"
            write_windows_csv(
                path, [Window(0, 0.0, 12, 0, 304, 540, 0.9)]
            )
            text = path.read_text(encoding="utf-8")
        self.assertIn("frame,time,x,y,w,h", text)
        self.assertIn("0,0.0,12,0,304,540", text)


if __name__ == "__main__":
    unittest.main()
