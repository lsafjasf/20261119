from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vp9_superframe.ivf import IvfPacket
from vp9_superframe.parser import inspect_ivf, inspect_packet


class SuperframeSampleTests(unittest.TestCase):
    def test_legal_sample_index_and_visible_semantics(self) -> None:
        _, reports = inspect_ivf(ROOT / "samples/legal.ivf")

        self.assertEqual(len(reports), 4)
        self.assertEqual([r.sequence for r in reports if r.corrupted], [])
        self.assertEqual(sum(r.visible_count for r in reports), 4)

        superframe = reports[1]
        self.assertTrue(superframe.is_superframe)
        self.assertEqual(superframe.marker, 0xC9)
        self.assertEqual(superframe.declared_frame_count, 2)
        self.assertEqual(superframe.sizes, [421, 232])
        self.assertEqual(superframe.size_sum, 653)
        self.assertEqual(superframe.payload_size, 653)
        self.assertEqual(superframe.index_size, 6)
        self.assertEqual(superframe.packet_size, superframe.size_sum + 6)
        self.assertEqual(superframe.visible_count, 1)
        self.assertEqual(superframe.last_presented_subframe_index, 1)

        hidden, shown = superframe.subframes
        self.assertEqual((hidden.offset, hidden.size), (0, 421))
        self.assertFalse(hidden.header.show_frame)
        self.assertFalse(hidden.displays)
        self.assertEqual((shown.offset, shown.size), (421, 232))
        self.assertTrue(shown.header.show_frame)
        self.assertTrue(shown.displays)
        self.assertTrue(all(subframe.indexed for subframe in superframe.subframes))

    def test_truncated_index_is_flagged_and_tail_is_salvaged(self) -> None:
        _, reports = inspect_ivf(ROOT / "samples/truncated-index.ivf")

        self.assertEqual([r.sequence for r in reports if r.corrupted], [1])
        self.assertEqual(sum(r.visible_count for r in reports), 4)

        superframe = reports[1]
        self.assertEqual(superframe.marker, 0xC8)
        self.assertEqual(superframe.declared_frame_count, 1)
        self.assertEqual(superframe.sizes, [421])
        self.assertEqual(superframe.size_sum, 421)
        self.assertEqual(superframe.payload_size, 653)
        self.assertEqual(len(superframe.subframes), 2)

        hidden, salvaged = superframe.subframes
        self.assertTrue(hidden.indexed)
        self.assertFalse(hidden.displays)
        self.assertFalse(salvaged.indexed)
        self.assertEqual((salvaged.offset, salvaged.size), (421, 232))
        self.assertTrue(salvaged.displays)
        self.assertEqual(superframe.last_presented_subframe_index, 1)
        self.assertTrue(
            any("unindexed tail" in error for error in superframe.errors)
        )

    def test_marker_without_matching_prefix_is_corrupt(self) -> None:
        bad = IvfPacket(7, 1234, b"\x82\x49\x83\x42\x00\x00\x00\x00\x00\x00\xc9")
        report = inspect_packet(bad)
        self.assertTrue(report.corrupted)
        self.assertTrue(any("marker mismatch" in error for error in report.errors))

    def test_cli_extracts_every_subframe_and_marks_last_presented(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "frames"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vp9_superframe",
                    str(ROOT / "samples/truncated-index.ivf"),
                    "--json",
                    "--extract-subframes",
                    str(output_dir),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["summary"]["visible_frame_count"], 4)
            self.assertEqual(payload["summary"]["corrupted_packet_sequences"], [1])

            manifest = json.loads((output_dir / "manifest.json").read_text())
            self.assertEqual(len(manifest), 5)
            salvaged = [
                item
                for item in manifest
                if item["packet_sequence"] == 1 and not item["indexed"]
            ]
            self.assertEqual(len(salvaged), 1)
            self.assertTrue(salvaged[0]["displays"])
            self.assertTrue(salvaged[0]["is_last_presented"])
            self.assertEqual(
                (output_dir / salvaged[0]["file"]).stat().st_size, 232
            )


if __name__ == "__main__":
    unittest.main()
