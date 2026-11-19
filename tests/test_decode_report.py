import json
import re
import tempfile
import unittest
from pathlib import Path

from hevc_tiles.analysis import analyze
from hevc_tiles.decode import DecodeError, find_ffmpeg, verify_tiles
from hevc_tiles.parser import parse_stream
from hevc_tiles.report import generate_report


ROOT = Path(__file__).resolve().parents[1]
INDEPENDENT = ROOT / "samples" / "tiles_independent.hevc"
ENTRYPOINTS = ROOT / "samples" / "tiles_entrypoints.hevc"
DEPENDENT = ROOT / "samples" / "tiles_loopfilter_dependency.hevc"


def _ffmpeg():
    try:
        return find_ffmpeg()
    except DecodeError:
        return None


class DecodeReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = _ffmpeg()

    def test_independent_subset_is_exact_and_masked(self):
        if not self.ffmpeg:
            self.skipTest("ffmpeg is not available")
        info = parse_stream(ENTRYPOINTS)
        analysis = analyze(info, 0)
        verification = verify_tiles(
            info,
            analysis.frame_slices,
            {1, 3},
            ffmpeg=self.ffmpeg,
        )
        self.assertIsNone(verification.error)
        self.assertTrue(verification.exact)
        self.assertEqual(set(verification.comparisons), {1, 3})
        self.assertTrue(all(item.mismatches == 0 for item in verification.comparisons.values()))

        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = generate_report(
                info, analysis, {1, 3}, temp_dir, verification
            )
            payload = json.loads((Path(temp_dir) / "report.json").read_text())
            self.assertEqual(payload["status"], "partial")
            self.assertEqual(payload["missing_tiles"], [0, 2])
            self.assertTrue((Path(temp_dir) / "partial_masked.png").is_file())
            page = report_path.read_text()
            self.assertIn("局部瓦片核验通过", page)
            self.assertIn("缺失区域始终显示为掩码", page)
            embedded = re.search(
                r'<script id="report-data" type="application/json">(.*?)</script>',
                page,
                re.S,
            )
            self.assertIsNotNone(embedded)
            self.assertEqual(json.loads(embedded.group(1))["status"], "partial")

    def test_dependency_config_does_not_generate_local_image(self):
        info = parse_stream(DEPENDENT)
        analysis = analyze(info, 0)
        self.assertFalse(analysis.local_allowed)
        with tempfile.TemporaryDirectory() as temp_dir:
            generate_report(info, analysis, {0, 2}, temp_dir, None)
            payload = json.loads((Path(temp_dir) / "report.json").read_text())
            self.assertEqual(payload["status"], "blocked")
            self.assertIsNone(payload["verification"])
            self.assertFalse((Path(temp_dir) / "partial_masked.png").exists())
            self.assertFalse((Path(temp_dir) / "partial_access_unit.hevc").exists())

    def test_all_tiles_are_verified_as_full_reference(self):
        if not self.ffmpeg:
            self.skipTest("ffmpeg is not available")
        info = parse_stream(INDEPENDENT)
        analysis = analyze(info, 0)
        verification = verify_tiles(
            info,
            analysis.frame_slices,
            {0, 1, 2, 3},
            ffmpeg=self.ffmpeg,
        )
        self.assertTrue(verification.exact)
        with tempfile.TemporaryDirectory() as temp_dir:
            generate_report(
                info,
                analysis,
                {0, 1, 2, 3},
                temp_dir,
                verification,
            )
            payload = json.loads((Path(temp_dir) / "report.json").read_text())
            self.assertEqual(payload["status"], "verified")
            self.assertEqual(payload["missing_tiles"], [])

    def test_selected_frame_uses_matching_full_decode(self):
        if not self.ffmpeg:
            self.skipTest("ffmpeg is not available")
        info = parse_stream(ENTRYPOINTS)
        analysis = analyze(info, 2)
        verification = verify_tiles(
            info,
            analysis.frame_slices,
            {1, 3},
            frame_index=2,
            ffmpeg=self.ffmpeg,
        )
        self.assertIsNone(verification.error)
        self.assertTrue(verification.exact)


if __name__ == "__main__":
    unittest.main()
