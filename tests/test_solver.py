from __future__ import annotations

import unittest

from portraitcrop.geometry import find_conflicts
from portraitcrop.metrics import compute_metrics
from portraitcrop.models import (
    AnnotationSet,
    Box,
    FrameAnnotation,
    Pin,
    Region,
    make_crop_spec,
)
from portraitcrop.solver import SolverConfig, solve_path


def moving_annotations(frame_count: int = 60, fps: int = 30) -> AnnotationSet:
    frames = []
    for index in range(frame_count):
        x = 120 + 8 * index
        frames.append(
            FrameAnnotation(
                index,
                index / fps,
                [Region("subject", Box(x, 80, 100, 240), 0.95)],
            )
        )
    return AnnotationSet(960, 540, fps, frames)


class SolverTests(unittest.TestCase):
    def test_static_subject_is_centered(self) -> None:
        fps = 30
        frames = [
            FrameAnnotation(index, index / fps, [Region("s", Box(430, 100, 100, 260))])
            for index in range(20)
        ]
        annotations = AnnotationSet(960, 540, fps, frames)
        crop = make_crop_spec(960, 540, "9:16")
        result = solve_path(annotations, crop, SolverConfig())
        self.assertEqual(result.status, "ok")
        expected = 430 + 50 - crop.width / 2.0
        self.assertLessEqual(abs(result.windows[10].x - expected), 3)

    def test_motion_limits_and_bounds(self) -> None:
        annotations = moving_annotations()
        crop = make_crop_spec(960, 540, "9:16")
        config = SolverConfig(max_speed_px_s=420, max_accel_px_s2=1400)
        result = solve_path(annotations, crop, config)
        metrics = compute_metrics(annotations, result, config)
        self.assertEqual(result.status, "ok")
        self.assertEqual(metrics["constraints"]["out_of_bounds_frames"], 0)
        self.assertEqual(metrics["constraints"]["speed_violation_frames"], 0)
        self.assertEqual(metrics["constraints"]["acceleration_violation_frames"], 0)

    def test_hard_pin_is_exact(self) -> None:
        annotations = moving_annotations()
        crop = make_crop_spec(960, 540, "9:16")
        result = solve_path(
            annotations,
            crop,
            SolverConfig(),
            [Pin(frame=20, x=300, hard=True)],
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.windows[20].x, 300)

    def test_infeasible_pin_reports_frame(self) -> None:
        fps = 30
        frames = [
            FrameAnnotation(index, index / fps, [Region("s", Box(0, 0, 20, 20))])
            for index in range(3)
        ]
        annotations = AnnotationSet(400, 200, fps, frames)
        crop = make_crop_spec(400, 200, "9:16")
        result = solve_path(
            annotations,
            crop,
            SolverConfig(max_speed_px_s=1, max_accel_px_s2=1),
            [Pin(frame=0, x=0), Pin(frame=1, x=100)],
        )
        self.assertEqual(result.status, "infeasible")
        self.assertEqual(result.failed_frame, 1)

    def test_conflict_lists_region_ids(self) -> None:
        frame = FrameAnnotation(
            0,
            0.0,
            [
                Region("left", Box(100, 100, 80, 220), 0.9),
                Region("right", Box(700, 120, 80, 220), 0.8),
            ],
        )
        annotations = AnnotationSet(960, 540, 30, [frame])
        crop = make_crop_spec(960, 540, "9:16")
        conflicts = find_conflicts(annotations, crop)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].region_ids, ("left", "right"))
        self.assertAlmostEqual(conflicts[0].overrun, 680 - crop.width)


if __name__ == "__main__":
    unittest.main()
