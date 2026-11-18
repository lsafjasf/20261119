import math
import unittest

from watermark_tracker.geometry import (
    Point,
    Quad,
    apply_homography,
    canonical_quad,
    homography_from_points,
    overlap_fraction,
    quad_from_center,
    rectangle,
)


class GeometryTests(unittest.TestCase):
    def test_homography_maps_all_quad_corners(self):
        quad = quad_from_center(Point(100, 80), 50, 0.2, 0.08)
        transform = homography_from_points(canonical_quad(12), quad.corners)
        projected = [
            apply_homography(transform, point) for point in canonical_quad(12)
        ]
        for expected, actual in zip(quad.corners, projected):
            self.assertAlmostEqual(expected.x, actual.x, places=6)
            self.assertAlmostEqual(expected.y, actual.y, places=6)

    def test_overlap_fraction(self):
        left = rectangle(0, 0, 10, 10)
        right = rectangle(5, 0, 10, 10)
        self.assertAlmostEqual(overlap_fraction(left, right), 0.5)
        self.assertAlmostEqual(overlap_fraction(left, rectangle(20, 0, 10, 10)), 0.0)

    def test_quad_measurements(self):
        quad = Quad(
            Point(0, 0),
            Point(10, 0),
            Point(10, 10),
            Point(0, 10),
        )
        self.assertEqual(quad.area(), 100)
        self.assertEqual(quad.mean_side(), 10)
        self.assertEqual(quad.center(), Point(5, 5))


if __name__ == "__main__":
    unittest.main()
