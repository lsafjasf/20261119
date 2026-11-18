"""Correctness tests for the multi-level peak index (small config)."""
import os
import random
import shutil
import tempfile
import unittest
from array import array

from peakindex import PeakIndex

SR = 8000
N = 1_000_003          # odd length: last bins are partial
FACTOR = 16
BPC = 64               # bins per chunk


def brute_minmax(samples, s0, s1, group):
    mins, maxs = array("h"), array("h")
    i = s0
    while i < s1:
        seg = samples[i:min(s1, i + group)]
        mins.append(min(seg))
        maxs.append(max(seg))
        i += group
    return mins, maxs


class PeakIndexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="pki-test-")
        rng = random.Random(42)
        cls.samples = array(
            "h", [rng.randint(-30000, 30000) for _ in range(N)])
        cls.impulse_pos = 777_777   # not aligned to any bin boundary
        cls.samples[cls.impulse_pos] = 32767
        cls.src = os.path.join(cls.tmp, "audio.raw")
        with open(cls.src, "wb") as fh:
            fh.write(cls.samples.tobytes())
        cls.idx_dir = os.path.join(cls.tmp, "idx")
        cls.idx = PeakIndex.build(cls.idx_dir, cls.src, SR,
                                  num_samples=N, factor=FACTOR,
                                  bins_per_chunk=BPC)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_levels(self):
        expect = N
        for lv in self.idx.levels:
            expect = (expect + FACTOR - 1) // FACTOR
            self.assertEqual(lv.num_bins, expect)
            self.assertEqual(lv.bin_size, FACTOR ** lv.level)
        self.assertEqual(self.idx.levels[-1].num_bins, 1)

    def test_query_minmax_matches_brute_force(self):
        rng = random.Random(1)
        for _ in range(30):
            a = rng.uniform(0, N / SR)
            b = a + rng.uniform(0.001, N / SR - a)
            max_bins = rng.choice([10, 100, 1000])
            res = self.idx.query(a, b, max_bins)
            self.assertGreater(res.bytes_read, 0)
            if res.mode == "samples":
                s0 = res.start_sample
                self.assertEqual(list(res.samples),
                                 list(self.samples[s0:s0 + res.bins]))
            else:
                if res.level == 0:
                    end = min(N, int(b * SR))  # raw: only the window
                else:
                    end = min(N, res.start_sample + res.bins * res.bin_size)
                em, ex = brute_minmax(self.samples, res.start_sample,
                                      end, res.bin_size)
                self.assertEqual(list(res.mins), list(em))
                self.assertEqual(list(res.maxs), list(ex))

    def test_sample_level_zoom_returns_real_samples(self):
        center = self.impulse_pos / SR
        res = self.idx.query(center - 0.005, center + 0.005, 200)
        self.assertEqual(res.mode, "samples")
        s0 = res.start_sample
        self.assertEqual(list(res.samples),
                         list(self.samples[s0:s0 + res.bins]))
        self.assertIn(32767, res.samples)

    def test_impulse_visible_at_all_levels(self):
        for lv in self.idx.levels:
            b = self.impulse_pos // lv.bin_size
            mins, maxs = self.idx.read_bins(lv.level, b, b + 1)
            self.assertEqual(maxs[0], 32767,
                             "impulse lost at level %d" % lv.level)
        res = self.idx.query(0, N / SR, 50)
        self.assertEqual(max(res.maxs), 32767)

    def test_persistence_roundtrip(self):
        idx2 = PeakIndex.open(self.idx_dir)
        r1 = self.idx.query(10.0, 42.5, 500)
        r2 = idx2.query(10.0, 42.5, 500)
        self.assertEqual(list(r1.mins), list(r2.mins))
        self.assertEqual(list(r1.maxs), list(r2.maxs))

    def test_replace_incremental_and_correct(self):
        start = 543_210
        seg = array("h", [12345] * 1000)
        reports = self.idx.replace(start, seg)
        self.samples[start:start + 1000] = seg
        with open(self.src, "r+b") as fh:
            fh.seek(2 * start)
            fh.write(seg.tobytes())
        total_bins = sum(r.bins_recomputed for r in reports)
        self.assertLess(total_bins, 100)
        for r in reports:
            self.assertLessEqual(r.chunks_rewritten, 3)
        ref_dir = os.path.join(self.tmp, "idx-ref")
        ref = PeakIndex.build(ref_dir, self.src, SR, num_samples=N,
                              factor=FACTOR, bins_per_chunk=BPC)
        for lv in self.idx.levels:
            am, ax = self.idx.read_bins(lv.level, 0, lv.num_bins)
            bm, bx = ref.read_bins(lv.level, 0, lv.num_bins)
            self.assertEqual(list(am), list(bm), "L%d mins" % lv.level)
            self.assertEqual(list(ax), list(bx), "L%d maxs" % lv.level)
        shutil.rmtree(ref_dir, ignore_errors=True)

    def test_replace_unchanged_parents_stop_propagation(self):
        start = 200_000
        seg = self.samples[start:start + 500]  # identical content
        reports = self.idx.replace(start, seg)
        with open(self.src, "r+b") as fh:
            fh.seek(2 * start)
            fh.write(seg.tobytes())
        self.assertEqual(reports[0].level, 1)
        self.assertEqual(reports[0].bins_changed, 0)
        self.assertEqual(len(reports), 1)


if __name__ == "__main__":
    unittest.main()
