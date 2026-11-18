import unittest

from watermark_tracker.detector import detect_markers
from watermark_tracker.geometry import Point, quad_from_center
from watermark_tracker.image import GrayImage, add_noise, adjust_brightness
from watermark_tracker.marker import render_marker


class DetectorTests(unittest.TestCase):
    def test_detects_rotated_perspective_marker(self):
        image = GrayImage(260, 200, 222)
        truth = quad_from_center(Point(130, 100), 62, 0.65, 0.10)
        render_marker(image, truth, 4242)
        add_noise(image, 2.0, 9)
        adjust_brightness(image, 0.65)
        detections = detect_markers(image)
        self.assertEqual(len(detections), 1)
        detection = detections[0]
        self.assertEqual(detection.marker_id, 4242)
        self.assertGreater(detection.confidence, 0.8)
        errors = [
            expected.distance_to(actual)
            for expected, actual in zip(truth.corners, detection.corners.corners)
        ]
        self.assertLess(max(errors), 2.0)

    def test_two_close_markers_keep_corner_orientation(self):
        image = GrayImage(300, 220, 230)
        left = quad_from_center(Point(105, 90), 46, 0.10, 0.08)
        right = quad_from_center(Point(190, 138), 46, -0.12, -0.06)
        render_marker(image, left, 7)
        render_marker(image, right, 42)
        detections = {item.marker_id: item for item in detect_markers(image)}
        self.assertEqual(set(detections), {7, 42})
        for marker_id, truth in ((7, left), (42, right)):
            error = sum(
                expected.distance_to(actual)
                for expected, actual in zip(
                    truth.corners, detections[marker_id].corners.corners
                )
            ) / 4.0
            self.assertLess(error, 1.5)


if __name__ == "__main__":
    unittest.main()
