from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import tempfile
import unittest

from mp4faststart.boxes import parse_box_tree, parse_top_level, walk_nodes
from mp4faststart.errors import FragmentedMP4Error, VerificationError
from mp4faststart.relayout import faststart
from mp4faststart.verify import compare_sample_bytes, read_samples
from samples.generate_samples import (
    box,
    make_fragmented,
    make_multitrack_unknown,
    make_stco_overflow_sparse,
)

UINT32_MAX = (1 << 32) - 1


class FastStartTests(unittest.TestCase):
    def test_multitrack_co64_and_unknown_boxes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_multitrack_unknown(root / "source.mp4")
            output = root / "output.mp4"
            result = faststart(source, output)

            before_types = [item.type for item in parse_top_level(source)[0]]
            after_boxes = parse_top_level(output)[0]
            after_types = [item.type for item in after_boxes]
            self.assertEqual(
                before_types, ["ftyp", "abcd", "mdat", "junk", "moov"]
            )
            self.assertEqual(after_types, ["ftyp", "abcd", "moov", "mdat", "junk"])
            self.assertEqual(result.upgraded_tables, 0)
            self.assertFalse(result.report["already_faststart"])
            self.assertEqual(result.report["moov"]["new_size"], result.report["moov"]["old_size"])

            verification = compare_sample_bytes(source, output)
            self.assertTrue(verification["ok"])
            self.assertEqual(verification["track_count"], 2)
            self.assertEqual(verification["sample_count"], 10)
            self.assertEqual(verification["byte_count"], 76)

            source_unknown = self._top_level_payload(source, "abcd")
            output_unknown = self._top_level_payload(output, "abcd")
            self.assertEqual(source_unknown, output_unknown)
            self.assertEqual(
                self._top_level_payload(source, "junk"),
                self._top_level_payload(output, "junk"),
            )
            chunk_rows = result.report["chunk_tables"]
            self.assertEqual([row["old_type"] for row in chunk_rows], ["stco", "co64"])
            self.assertEqual([row["new_type"] for row in chunk_rows], ["stco", "co64"])
            for track in read_samples(output):
                self.assertGreater(len(track.samples), 0)

    def test_stco_overflow_grows_moov_and_upgrades_to_co64(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_stco_overflow_sparse(root / "overflow.mp4")
            output = root / "overflow-faststart.mp4"
            result = faststart(source, output)

            self.assertEqual(result.upgraded_tables, 1)
            chunk = result.report["chunk_tables"][0]
            self.assertEqual(chunk["old_type"], "stco")
            self.assertEqual(chunk["new_type"], "co64")
            self.assertTrue(chunk["upgraded"])
            self.assertGreater(chunk["first_new_offset"], UINT32_MAX)
            self.assertEqual(
                result.report["moov"]["new_size"],
                result.report["moov"]["old_size"] + 4,
            )
            self.assertEqual(output.stat().st_size, source.stat().st_size + 4)
            verification = compare_sample_bytes(source, output)
            self.assertTrue(verification["ok"])
            self.assertEqual(verification["byte_count"], 64)
            allocated = getattr(output.stat(), "st_blocks", 0) * 512
            self.assertLess(allocated, 1024 * 1024)

            chunk_boxes = self._chunk_boxes(output)
            self.assertEqual([box.type for box in chunk_boxes], ["co64"])

    def test_fragmented_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_fragmented(root / "fragmented.mp4")
            output = root / "output.mp4"
            with self.assertRaisesRegex(FragmentedMP4Error, "fragmented"):
                faststart(source, output)
            self.assertFalse(output.exists())

    def test_mvex_without_moof_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "init.mp4"
            source.write_bytes(
                b""
                + struct.pack(">I4s", 8, b"ftyp")
                + box(b"moov", box(b"mvex", b""))
                + box(b"mdat", b"\x00")
            )
            with self.assertRaisesRegex(FragmentedMP4Error, "mvex"):
                faststart(source, root / "output.mp4")

    def test_output_is_idempotent_and_samples_still_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_multitrack_unknown(root / "source.mp4")
            first = root / "first.mp4"
            second = root / "second.mp4"
            faststart(source, first)
            result = faststart(first, second)
            self.assertTrue(result.already_faststart)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertTrue(compare_sample_bytes(source, second)["ok"])

    def test_verifier_detects_corrupted_sample(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_multitrack_unknown(root / "source.mp4")
            output = root / "output.mp4"
            faststart(source, output)
            track = read_samples(output)[0]
            sample_offset = track.samples[0].offset
            data = bytearray(output.read_bytes())
            data[sample_offset] ^= 0xFF
            output.write_bytes(data)
            with self.assertRaisesRegex(VerificationError, "bytes differ"):
                compare_sample_bytes(source, output)

    @staticmethod
    def _top_level_payload(path: Path, box_type: str) -> bytes:
        boxes, _ = parse_top_level(path)
        matching = [entry for entry in boxes if entry.type == box_type]
        if len(matching) != 1:
            raise AssertionError(f"expected one {box_type} box")
        entry = matching[0]
        data = path.read_bytes()
        return data[entry.offset : entry.end]

    @staticmethod
    def _chunk_boxes(path: Path):
        boxes, _ = parse_top_level(path)
        moov = next(entry for entry in boxes if entry.type == "moov")
        data = path.read_bytes()[moov.offset : moov.end]
        root = parse_box_tree(data)[0]
        return [
            node for node in walk_nodes([root]) if node.type in {"stco", "co64"}
        ]


if __name__ == "__main__":
    unittest.main()
