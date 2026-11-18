import json
import tempfile
import unittest
from pathlib import Path

from adts_parser.cli import main
from adts_parser import parse_stream
from samples.make_samples import make_adts_frame, make_id3v2


class CliTests(unittest.TestCase):
    def test_cli_exports_index_and_segments_reread_independently(self):
        id3_a = make_id3v2(b"first")
        id3_b = make_id3v2(b"second-tag")
        track_a = b"".join(make_adts_frame(payload_size=80) for _ in range(2))
        track_b = b"".join(
            make_adts_frame(payload_size=60, sample_rate_index=8, channel_config=1)
            for _ in range(3)
        )
        data = id3_a + track_a + id3_b + track_b

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            stream_path = tmp_path / "concat.aac"
            index_path = tmp_path / "index.json"
            stream_path.write_bytes(data)

            rc = main([str(stream_path), "--json", str(index_path)])
            self.assertEqual(rc, 0)

            index = json.loads(index_path.read_text())
            self.assertEqual(index["segment_count"], 2)

            # Re-read each segment purely from the exported offsets.
            raw = stream_path.read_bytes()
            reread_slices = []
            for entry in index["segments"]:
                chunk = raw[entry["start"] : entry["end"]]
                reread_slices.append(chunk)
                sub = parse_stream(chunk, source="reread")
                self.assertEqual(len(sub.segments), 1)
                self.assertIsNotNone(sub.segments[0].id3)
                self.assertEqual(
                    sub.segments[0].sample_rate, entry["sample_rate"]
                )
                self.assertEqual(
                    sub.segments[0].sample_count, entry["sample_count"]
                )

            # Slices are contiguous, lossless and never share ID3 bytes.
            self.assertEqual(b"".join(reread_slices), data)
            self.assertTrue(reread_slices[0].startswith(b"ID3"))
            self.assertTrue(reread_slices[1].startswith(b"ID3"))


if __name__ == "__main__":
    unittest.main()
