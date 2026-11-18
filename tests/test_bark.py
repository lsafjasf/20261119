import unittest

import numpy as np

from critical_band.bark import (
    BARK_BANDS, bark_from_freq, freq_from_bark, assign_band,
)


class TestBarkMap(unittest.TestCase):
    def test_table_shape_and_contiguity(self):
        self.assertEqual(len(BARK_BANDS), 24)
        for i in range(1, 24):
            self.assertAlmostEqual(BARK_BANDS[i].lower,
                                   BARK_BANDS[i - 1].upper)
        self.assertEqual(BARK_BANDS[0].lower, 20.0)
        self.assertEqual(BARK_BANDS[-1].upper, 15500.0)

    def test_center_between_bounds(self):
        for b in BARK_BANDS:
            self.assertLessEqual(b.lower, b.center)
            self.assertLessEqual(b.center, b.upper)

    def test_assign_band_explicit_table(self):
        self.assertEqual(assign_band(50.0), 1)
        self.assertEqual(assign_band(99.9), 1)
        # 左闭右开：100 Hz 仍是第 1 带上界，归入第 2 带
        self.assertEqual(assign_band(100.0), 2)
        # 1000 Hz 是第 9 带中心
        self.assertEqual(assign_band(999.0), 9)
        self.assertEqual(assign_band(15000.0), 24)
        self.assertEqual(assign_band(10.0), 0)
        self.assertEqual(assign_band(16000.0), 0)

    def test_vectorized_assign(self):
        idx = assign_band(np.array([50.0, 150.0, 13500.0]))
        np.testing.assert_array_equal(idx, [1, 2, 24])

    def test_analytic_formula_known_points(self):
        self.assertAlmostEqual(bark_from_freq(1000.0), 8.5, delta=0.3)
        self.assertAlmostEqual(bark_from_freq(100.0), 1.0, delta=0.25)

    def test_inverse_freq_bark(self):
        for f in (100.0, 500.0, 1000.0, 4000.0, 10000.0):
            z = bark_from_freq(f)
            self.assertAlmostEqual(freq_from_bark(z), f, delta=0.5)


if __name__ == "__main__":
    unittest.main()
