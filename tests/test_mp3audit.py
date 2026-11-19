from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from mp3audit.header import HeaderError, parse_header
from mp3audit.parser import audit_bytes, audit_file
from mp3audit.sideinfo import parse_side_info
from mp3_samples import cbr_stream, header_bytes, side_info_bytes


ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"


class HeaderAndSideInfoTests(unittest.TestCase):
    def test_mpeg1_layer3_header(self) -> None:
        header = parse_header(b"\xff\xfb\x90\x00")
        self.assertEqual(header.version_name, "MPEG-1")
        self.assertEqual(header.layer_name, "Layer III")
        self.assertEqual(header.bitrate, 128_000)
        self.assertEqual(header.sample_rate, 44_100)
        self.assertEqual(header.frame_length, 417)
        self.assertEqual(header.side_info_size, 32)
        self.assertEqual(header.main_data_offset, 36)

    def test_reserved_fields_are_rejected(self) -> None:
        cases = (
            b"\xff\xeb\x90\x00",
            b"\xff\xf9\x90\x00",
            b"\xff\xfb\xf0\x00",
            b"\xff\xfb\x9c\x00",
            b"\xff\xfb\x90\x02",
        )
        for raw in cases:
            with self.subTest(raw=raw.hex()):
                with self.assertRaises(HeaderError):
                    parse_header(raw)

    def test_side_info_layout(self) -> None:
        raw = bytearray(417)
        raw[:4] = b"\xff\xfb\x90\x00"
        raw[4:36] = bytes(32)
        raw[4] = 0x80
        header = parse_header(raw)
        side = parse_side_info(bytes(raw[4:]), header)
        self.assertIsNotNone(side)
        assert side is not None
        self.assertEqual(side.main_data_begin, 256)
        self.assertEqual(side.size, 32)
        self.assertEqual(len(side.granules), 4)
        self.assertEqual(side.part2_3_bits, 0)

    def test_mpeg2_mono_side_info_size(self) -> None:
        header = parse_header(
            header_bytes(version=2, bitrate_index=9, mode=3)
        )
        self.assertEqual(header.version_name, "MPEG-2")
        self.assertEqual(header.side_info_size, 9)
        self.assertEqual(header.samples_per_frame, 576)
        side = parse_side_info(
            side_info_bytes(
                version=2,
                mode=3,
                main_data_begin=100,
                part2_3_length=200,
            ),
            header,
        )
        assert side is not None
        self.assertEqual(side.main_data_begin, 100)
        self.assertEqual(side.part2_3_bits, 200)
        self.assertEqual(side.reservoir_limit, 255)


class SampleBoundaryTests(unittest.TestCase):
    def test_cbr_frame_boundaries_and_bitrate(self) -> None:
        result = audit_file(SAMPLES / "cbr_128k.mp3")
        self.assertEqual(result.stream_kind, "CBR")
        self.assertEqual(result.frame_count, 12)
        self.assertEqual(set(result.bitrate_histogram), {128_000})
        self.assertFalse(result.damaged_frames)
        self.assertEqual(result.diagnostics, [])
        self.assertEqual(result.frames[0].header.offset, 0)
        for index, frame in enumerate(result.frames):
            self.assertEqual(frame.index, index)
            self.assertEqual(frame.next_offset, frame.header.end_offset)
            if index:
                self.assertEqual(
                    frame.header.offset, result.frames[index - 1].header.end_offset
                )
        expected = result.total_frame_bytes * 8 / result.duration
        self.assertAlmostEqual(result.actual_bitrate, expected, places=3)
        self.assertAlmostEqual(result.actual_bitrate / 1000, 127.808, places=3)

    def test_vbr_count_bitrate_and_reservoir_ranges(self) -> None:
        result = audit_file(SAMPLES / "vbr.mp3")
        self.assertEqual(result.stream_kind, "VBR")
        self.assertEqual(result.frame_count, 13)
        self.assertEqual(len(result.bitrate_histogram), 7)
        self.assertFalse(result.damaged_frames)
        self.assertGreater(result.actual_bitrate / 1000, 160)
        for frame in result.frames[1:]:
            self.assertIsNotNone(frame.reservoir)
            assert frame.reservoir is not None
            self.assertGreater(frame.reservoir.main_data_begin, 0)
            self.assertFalse(frame.reservoir.underflow)
            self.assertIsNotNone(frame.reservoir.history_frame_start)
        first_use = result.frames[1].reservoir
        assert first_use is not None
        self.assertEqual(first_use.history_frame_start, 0)
        self.assertEqual(first_use.history_frame_end, 1)

    def test_id3_boundaries_are_excluded_from_audio(self) -> None:
        result = audit_file(SAMPLES / "id3_cbr.mp3")
        self.assertEqual(result.audio_start, 130)
        self.assertEqual(result.audio_end, 4303)
        self.assertEqual([tag.kind for tag in result.tags], ["ID3v2", "ID3v1"])
        self.assertEqual(result.frames[0].header.offset, 130)
        self.assertEqual(result.frames[-1].next_offset, 4303)
        self.assertEqual(result.frame_count, 10)
        self.assertEqual(result.total_frame_bytes, 4173)
        self.assertFalse(result.damaged_frames)

    def test_prefill_is_detected_before_decode(self) -> None:
        result = audit_file(SAMPLES / "prefill.mp3")
        codes = [diagnostic.code for diagnostic in result.frames[0].diagnostics]
        self.assertIn("prefill_missing_history", codes)
        self.assertTrue(result.frames[0].reservoir.prefill)
        self.assertTrue(result.frames[0].reservoir.underflow)
        self.assertEqual(result.damaged_frames, [result.frames[0]])

    def test_length_drift_and_resynchronisation(self) -> None:
        result = audit_file(SAMPLES / "drift.mp3")
        self.assertEqual(result.frame_count, 10)
        drifted = result.frames[3]
        self.assertEqual(drifted.length_drift, 3)
        self.assertEqual(drifted.next_offset, drifted.header.end_offset + 3)
        self.assertIn(
            "frame_length_drift",
            [diagnostic.code for diagnostic in drifted.diagnostics],
        )
        self.assertEqual(result.frames[4].header.offset, drifted.next_offset)

    def test_sync_word_alone_is_not_a_frame(self) -> None:
        result = audit_file(SAMPLES / "false_sync.mp3")
        self.assertEqual(result.frame_count, 8)
        self.assertEqual(result.frames[0].header.offset, 16)
        self.assertEqual(result.diagnostics[0].code, "leading_junk")
        self.assertEqual(result.diagnostics[0].details["bytes"], 16)

    def test_random_sync_without_following_frame_is_rejected(self) -> None:
        result = audit_bytes(b"\xff\xfb\x90\x00" + bytes(100))
        self.assertEqual(result.frame_count, 0)
        self.assertEqual(result.diagnostics[0].code, "no_valid_frames")
        self.assertEqual(result.diagnostics[0].details["sync_candidates"], 1)

    def test_truncated_last_frame_keeps_prior_complete_frames(self) -> None:
        fixture = cbr_stream(frame_count=3)
        result = audit_bytes(fixture.data[:-100])
        self.assertEqual(result.frame_count, 3)
        self.assertEqual(result.frames[2].status, "damaged")
        self.assertEqual(
            result.frames[2].diagnostics[0].code, "truncated_frame"
        )
        self.assertEqual(result.frames[0].status, "ok")

    def test_side_info_semantics_mark_frame_before_decode(self) -> None:
        result = audit_file(SAMPLES / "semantic_damage.mp3")
        self.assertEqual(result.frame_count, 3)
        self.assertEqual(result.damaged_frames, [result.frames[1]])
        reasons = {
            diagnostic.details.get("reason")
            for diagnostic in result.frames[1].diagnostics
        }
        self.assertIn("invalid_block_type", reasons)
        self.assertIn(
            "bad_side_info_semantics",
            [diagnostic.code for diagnostic in result.diagnostics],
        )


class CliTests(unittest.TestCase):
    def test_json_cli(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "mp3_audit.py"), str(SAMPLES / "vbr.mp3"), "--json"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["frame_count"], 13)
        self.assertEqual(report["stream_kind"], "VBR")
        self.assertIn("reservoir", report["frames"][1])

    def test_fail_on_damage(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "mp3_audit.py"),
                str(SAMPLES / "prefill.mp3"),
                "--fail-on-damage",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("prefill_missing_history", completed.stdout)


if __name__ == "__main__":
    unittest.main()
