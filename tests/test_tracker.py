import unittest

from watermark_tracker.geometry import Point, Quad
from watermark_tracker.tracker import FrameObservation, Tracker


def square(cx, cy, size=40.0):
    half = size / 2.0
    return Quad(
        Point(cx - half, cy - half),
        Point(cx + half, cy - half),
        Point(cx + half, cy + half),
        Point(cx - half, cy + half),
    )


class TrackerTests(unittest.TestCase):
    def test_crossing_ids_do_not_swap_during_prediction(self):
        tracker = Tracker(max_prediction_frames=4)
        output = []
        for frame in range(10):
            first = square(100 + frame * 10, 100)
            second = square(190 - frame * 10, 103)
            observations = [
                FrameObservation(frame, 11, first, 0.95),
                FrameObservation(frame, 99, second, 0.95),
            ]
            if frame in (4, 5, 6):
                observations = observations[1:]
            output.extend(tracker.process_frame(frame, observations))
        grouped = tracker.result().by_track()
        self.assertEqual(len(grouped), 2)
        for records in grouped.values():
            ids = {record.last_decoded_id for record in records}
            self.assertIn(ids, ({11}, {99}))
        predicted = [
            record
            for record in output
            if record.visibility == "occluded"
        ]
        self.assertEqual(len(predicted), 3)
        for record in predicted:
            self.assertIsNone(record.decoded_id)
            self.assertIsNone(record.decode_confidence)
            self.assertTrue(record.predicted)

    def test_track_is_lost_after_prediction_horizon(self):
        tracker = Tracker(max_prediction_frames=2)
        frame_zero = square(50, 50)
        tracker.process_frame(
            0, [FrameObservation(0, 5, frame_zero, 0.9)]
        )
        records = []
        for frame in range(1, 5):
            records.extend(tracker.process_frame(frame, []))
        self.assertEqual(
            [record.visibility for record in records],
            ["occluded", "occluded", "lost"],
        )


if __name__ == "__main__":
    unittest.main()
