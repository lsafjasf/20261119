import csv
import json
import tempfile
import unittest
from pathlib import Path

from watermark_tracker.detector import detect_markers
from watermark_tracker.evaluation import evaluate_all
from watermark_tracker.report import write_outputs
from watermark_tracker.scenario import generate_crossing_clip, load_ground_truth
from watermark_tracker.tracker import FrameObservation, Tracker


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clip = generate_crossing_clip(frame_count=48)
        cls.tracker = Tracker(max_prediction_frames=5)
        for frame, image in enumerate(cls.clip.images):
            observations = [
                FrameObservation(
                    frame, item.marker_id, item.corners, item.confidence
                )
                for item in detect_markers(image)
            ]
            cls.tracker.process_frame(frame, observations)
        cls.result = cls.tracker.result()
        cls.metrics = evaluate_all(cls.result, cls.clip.truth)

    def test_detection_corner_accuracy(self):
        detection = self.metrics["detection"]
        self.assertGreaterEqual(detection["visible_recall"], 0.98)
        self.assertGreater(detection["mean_px"], 0.0)
        self.assertLess(detection["mean_px"], 2.0)
        self.assertLess(detection["max_px"], 4.0)
        self.assertEqual(detection["false_positives"], 0)

    def test_crossing_and_occlusion_semantics(self):
        tracking = self.metrics["tracking"]
        self.assertEqual(tracking["track_count"], 2)
        self.assertEqual(tracking["id_switches"], 0)
        self.assertEqual(tracking["id_switches_in_crossing_window"], 0)
        self.assertFalse(tracking["swapped_in_crossing_window"])
        self.assertEqual(tracking["fresh_decodes_while_occluded"], 0)
        self.assertGreaterEqual(tracking["predicted_occlusion_frames"], 3)
        self.assertTrue(all(tracking["checks"].values()))

    def test_writes_trajectory_and_visibility_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "clip"
            self.clip.save(dataset)
            self.clip.write_avi(dataset / "clip.avi")
            avi_header = (dataset / "clip.avi").read_bytes()[:12]
            self.assertEqual(avi_header[:4], b"RIFF")
            self.assertEqual(avi_header[8:12], b"AVI ")
            truth = load_ground_truth(dataset / "ground_truth.json")
            self.assertEqual(len(truth), 48)
            write_outputs(self.result, truth, root / "results")
            self.assertTrue((root / "results" / "tracks.json").is_file())
            self.assertTrue((root / "results" / "trajectory.csv").is_file())
            self.assertTrue((root / "results" / "visibility.csv").is_file())
            summary = json.loads(
                (root / "results" / "summary.json").read_text(encoding="utf-8")
            )
            self.assertTrue(summary["tracking"]["checks"]["ids_stable"])
            with (root / "results" / "trajectory.csv").open(
                encoding="utf-8", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))
            predicted = [row for row in rows if row["predicted"] == "1"]
            self.assertTrue(predicted)
            self.assertTrue(all(row["decoded_id"] == "" for row in predicted))
            self.assertTrue(all(row["decode_confidence"] == "" for row in predicted))


if __name__ == "__main__":
    unittest.main()
