from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _vpxdec_available() -> bool:
    return bool(
        os.environ.get("VPXDEC")
        or (ROOT / ".tools/root/usr/bin/vpxdec").is_file()
        or shutil.which("vpxdec")
    )


@unittest.skipUnless(_vpxdec_available(), "independent vpxdec is not installed")
class VpxdecVisibleCountTests(unittest.TestCase):
    def _verify(self, sample: str, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/verify_visible.py"),
                str(ROOT / "samples" / sample),
                *extra,
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_legal_visible_count_matches_vpxdec(self) -> None:
        result = self._verify("legal.ivf")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("parser_visible_frames=4", result.stdout)
        self.assertIn("vpxdec_showed_frames=4", result.stdout)

    def test_truncated_index_drops_a_frame_only_in_vpxdec(self) -> None:
        result = self._verify("truncated-index.ivf", "--expect-dropped-tail")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("parser_visible_frames=4", result.stdout)
        self.assertIn("parser_corrupted_packets=[1]", result.stdout)
        self.assertIn("vpxdec_showed_frames=3", result.stdout)


if __name__ == "__main__":
    unittest.main()
