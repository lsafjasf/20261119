from __future__ import annotations

import json
import unittest
from pathlib import Path

from flac_inspect import inspect_file
from flac_inspect.crc import crc8, crc16
from flac_inspect.report import as_dict, render_text

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


class CrcTests(unittest.TestCase):
    def test_known_crc_vectors(self) -> None:
        self.assertEqual(crc8(b"123456789"), 0xF4)
        self.assertEqual(crc16(b"123456789"), 0xFEE8)


class ValidFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = inspect_file(SAMPLES / "valid.flac")

    def test_streaminfo_and_pcm_md5(self) -> None:
        stream = self.result.stream_info
        assert stream is not None
        self.assertEqual(stream.sample_rate, 44100)
        self.assertEqual(stream.channels, 2)
        self.assertEqual(stream.bits_per_sample, 16)
        self.assertEqual(stream.total_samples, 3976)
        self.assertEqual(stream.md5_hex, "363e1fbf713fc54843552132ed4d87b5")
        self.assertTrue(self.result.md5_complete)
        self.assertTrue(self.result.md5_matches)
        self.assertEqual(self.result.decoded_md5, stream.md5_hex)

    def test_all_frames_have_valid_crc_and_sequence(self) -> None:
        self.assertEqual(len(self.result.frames), 4)
        self.assertFalse(self.result.recoveries)
        self.assertFalse(self.result.invalid_frames)
        assignments = [frame.channel_assignment for frame in self.result.frames]
        self.assertEqual(
            assignments,
            ["independent-2", "independent-2", "left-side", "mid-side"],
        )
        for frame in self.result.frames:
            self.assertTrue(frame.header_crc_ok)
            self.assertTrue(frame.frame_crc_ok)
            self.assertTrue(frame.structurally_valid)
            self.assertTrue(frame.valid_audio)
            self.assertEqual(frame.channels, 2)
        self.assertEqual(
            [frame.header_number for frame in self.result.frames], [0, 1, 2, 3]
        )
        self.assertEqual(
            [frame.sample_number for frame in self.result.frames],
            [0, 1024, 2048, 3072],
        )
        self.assertEqual(
            [frame.block_size for frame in self.result.frames],
            [1024, 1024, 1024, 904],
        )

    def test_subframe_encodings_are_reported(self) -> None:
        kinds = {
            subframe.kind
            for frame in self.result.frames
            for subframe in frame.subframes
        }
        self.assertEqual(kinds, {"CONSTANT", "VERBATIM", "FIXED", "LPC"})
        text = render_text(self.result)
        self.assertIn("encoding=VERBATIM", text)
        self.assertIn("encoding=LPC", text)
        self.assertIn("channel_assignment=independent-2", text)
        self.assertIn("complete PCM MD5: 363e1fbf713fc54843552132ed4d87b5", text)
        self.assertIn("result: MATCH", text)


class DamagedFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.header = inspect_file(SAMPLES / "header_corrupt.flac")
        cls.payload = inspect_file(SAMPLES / "payload_corrupt.flac")

    def test_header_crc_and_frame_crc_fail(self) -> None:
        damaged = self.header.frames[1]
        self.assertFalse(damaged.header_crc_ok)
        self.assertEqual(damaged.header_crc_expected, 0x00)
        self.assertEqual(damaged.header_crc_calculated, 0x15)
        self.assertFalse(damaged.frame_crc_ok)
        self.assertEqual(damaged.frame_crc_expected, 0x9ADD)
        self.assertEqual(damaged.frame_crc_calculated, 0x2F26)
        self.assertFalse(damaged.structurally_valid)
        self.assertFalse(damaged.valid_audio)

    def test_payload_damage_keeps_header_crc_but_fails_frame_crc(self) -> None:
        damaged = self.payload.frames[1]
        self.assertTrue(damaged.header_crc_ok)
        self.assertFalse(damaged.frame_crc_ok)
        self.assertEqual(damaged.frame_crc_expected, 0x9ADD)
        self.assertEqual(damaged.frame_crc_calculated, 0x1099)
        self.assertTrue(damaged.subframes)
        self.assertFalse(damaged.structurally_valid)
        self.assertFalse(damaged.valid_audio)
        self.assertIsNone(self.payload.decoded_md5)
        self.assertFalse(self.payload.md5_complete)

    def test_recovery_skips_payload_sync_and_finds_next_frame(self) -> None:
        for result, fake_offset in ((self.header, 0x148), (self.payload, 0x1AC)):
            self.assertEqual(len(result.recoveries), 1)
            recovery = result.recoveries[0]
            self.assertEqual(recovery.damaged_offset, 0x7A)
            self.assertEqual(recovery.resume_offset, 0x1084)
            self.assertEqual([item.offset for item in recovery.rejected], [fake_offset])
            self.assertIn("CRC8", recovery.rejected[0].reason)

            recovered = result.frames[2]
            self.assertEqual(recovered.offset, recovery.resume_offset)
            self.assertTrue(recovered.header_crc_ok)
            self.assertTrue(recovered.frame_crc_ok)
            self.assertTrue(recovered.structurally_valid)
            self.assertTrue(recovered.sequence_status.startswith("GAP"))
            self.assertTrue(result.frames[3].frame_crc_ok)

    def test_json_is_serializable_and_contains_both_crcs(self) -> None:
        payload = as_dict(self.payload)
        encoded = json.dumps(payload)
        self.assertIn('"header_crc"', encoded)
        self.assertIn('"frame_crc"', encoded)
        self.assertFalse(payload["frames"][1]["frame_crc"]["ok"])


if __name__ == "__main__":
    unittest.main()
