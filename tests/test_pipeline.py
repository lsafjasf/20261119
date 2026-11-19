"""Acceptance tests for the multi-exposure HDR merge pipeline."""

import unittest

import numpy as np

from hdr_merge.evaluation import (
    ghost_energy,
    motion_iou,
    static_relative_error,
    unrecoverable_check,
)
from hdr_merge.merge import merge_exposures
from hdr_merge.outlier import reject_outliers
from hdr_merge.response import estimate_response
from hdr_merge.synthetic import generate_test_sequence
from hdr_merge.weights import Z_MAX


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seq = generate_test_sequence()
        cls.result = merge_exposures(cls.seq.images, cls.seq.exposures)
        cls.kept = np.delete(cls.seq.images, cls.result.rejected, axis=0)
        cls.kept_exposures = np.delete(
            cls.seq.exposures, cls.result.rejected
        )

    def test_response_curve_recovered(self):
        g = estimate_response(
            self.kept, self.kept_exposures, n_samples=1500, lam=40.0,
            static_mask=~self.result.motion_mask,
        )
        for c in range(3):
            hist = np.bincount(self.kept[..., c].ravel(), minlength=256)
            observed = np.array(
                [z for z in range(8, 240) if hist[z] >= 3]
            )
            rmse = np.sqrt(
                np.mean((g[observed, c] - self.seq.crf[observed, c]) ** 2)
            )
            self.assertLess(rmse, 0.25)
        # The saturated bright tail can wiggle on clipped observations; the
        # usable (non-clipped) body of the curve must be monotone.
        self.assertTrue(np.all(np.diff(g[:220, 1]) >= 0))

    def test_outlier_rejected(self):
        self.assertIn(self.seq.outlier_index, self.result.rejected.tolist())
        self.assertLessEqual(self.result.rejected.size, 2)

    def test_static_relative_error_small(self):
        stats, _ = static_relative_error(
            self.result.irradiance,
            self.seq.irradiance,
            self.seq.static_mask,
            self.result.valid,
        )
        self.assertLess(stats["median"], 0.03)
        self.assertLess(stats["p95"], 0.08)

    def test_motion_region_detected(self):
        iou = motion_iou(self.result.motion_mask, self.seq.motion_gt)
        self.assertGreater(iou, 0.55)

    def test_no_ghost_in_motion_band(self):
        ghost = ghost_energy(
            self.kept,
            self.kept_exposures,
            self.result.g,
            self.result.irradiance,
            self.seq.motion_gt,
            self.result.reference,
            self.result.motion_mask,
            self.result.valid,
        )
        self.assertTrue(ghost["ghost_free"])
        self.assertGreaterEqual(ghost["reference_match_fraction"], 0.99)

    def test_permanently_clipped_is_unrecoverable(self):
        all_white = (self.kept == Z_MAX).all(axis=0).all(axis=-1)
        all_black = (self.kept == 0).all(axis=0).all(axis=-1)
        clipped = all_white | all_black
        self.assertGreater(clipped.sum(), 0)
        self.assertFalse(np.any(clipped & self.result.valid))
        report = unrecoverable_check(self.kept, self.result.valid)
        self.assertTrue(report["all_flagged_unrecoverable"])

    def test_output_dtypes_and_shapes(self):
        h, w = self.seq.images.shape[1:3]
        self.assertEqual(
            self.result.irradiance.shape, (h, w, 3)
        )
        self.assertEqual(self.result.irradiance.dtype, np.float64)
        self.assertEqual(self.result.valid.shape, (h, w))
        self.assertEqual(self.result.valid.dtype, bool)


if __name__ == "__main__":
    unittest.main()
