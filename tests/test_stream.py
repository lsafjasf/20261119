import json
import unittest

from adts_parser import parse_stream
from samples.make_samples import make_adts_frame, make_id3v2


def frames(count, **kwargs):
    return b"".join(make_adts_frame(payload_size=100, **kwargs) for _ in range(count))


class SegmentationTests(unittest.TestCase):
    def test_id3_boundary_splits_tracks_and_keeps_tag_attached(self):
        id3_a = make_id3v2(b"AAAA")
        id3_b = make_id3v2(b"BBBBBB")
        track_a = frames(3, sample_rate_index=4, channel_config=2)
        track_b = frames(2, sample_rate_index=8, channel_config=1)
        data = id3_a + track_a + id3_b + track_b

        index = parse_stream(data, source="mem")
        self.assertEqual(len(index.segments), 2)

        seg_a, seg_b = index.segments
        self.assertEqual(seg_a.start, 0)
        self.assertEqual(seg_a.id3.total_size, len(id3_a))
        self.assertEqual(seg_a.end, len(id3_a) + len(track_a))
        self.assertEqual(seg_b.start, seg_a.end)
        self.assertEqual(seg_b.sample_rate, 16000)
        self.assertEqual(seg_b.sample_count, 2 * 1024)

        # Each segment can be re-read independently; re-parsing a slice must
        # not consume the neighbour's ID3 tag.
        slice_a = index.extract_segment_bytes(data, 0)
        slice_b = index.extract_segment_bytes(data, 1)
        self.assertEqual(slice_a, id3_a + track_a)
        self.assertEqual(slice_b, id3_b + track_b)
        re_a = parse_stream(slice_a, source="slice-a")
        re_b = parse_stream(slice_b, source="slice-b")
        self.assertEqual(len(re_a.segments), 1)
        self.assertEqual(len(re_b.segments), 1)
        self.assertIsNotNone(re_a.segments[0].id3)
        self.assertIsNotNone(re_b.segments[0].id3)

    def test_parameter_change_without_id3_splits_segments(self):
        data = frames(2, sample_rate_index=4) + frames(2, sample_rate_index=8)
        index = parse_stream(data)
        self.assertEqual(len(index.segments), 2)
        self.assertEqual(index.segments[0].sample_rate, 44100)
        self.assertEqual(index.segments[1].sample_rate, 16000)

        merged = parse_stream(data, split_on_param_change=False)
        self.assertEqual(len(merged.segments), 1)

    def test_duration_and_sample_count(self):
        data = frames(4, sample_rate_index=4)  # 44100 Hz
        index = parse_stream(data)
        seg = index.segments[0]
        self.assertEqual(seg.sample_count, 4 * 1024)
        self.assertAlmostEqual(seg.duration_seconds, (4 * 1024) / 44100, places=9)

    def test_mixed_crc_modes_stay_in_one_segment_with_correct_header_sizes(self):
        data = (
            make_adts_frame(payload_size=50, protection_absent=1)
            + make_adts_frame(payload_size=50, protection_absent=0)
        )
        index = parse_stream(data)
        self.assertEqual(len(index.segments), 1)
        sizes = [frame.header_size for frame in index.segments[0].frames]
        self.assertEqual(sizes, [7, 9])


class TruncationTests(unittest.TestCase):
    def test_truncated_tail_frame_excluded_from_playable_duration(self):
        good = frames(3, sample_rate_index=8)
        full = make_adts_frame(payload_size=100, sample_rate_index=8)
        cut = full[:20]  # header complete, payload missing
        data = good + cut

        index = parse_stream(data)
        self.assertEqual(len(index.truncated), 1)
        trunc = index.truncated[0]
        self.assertEqual(trunc.offset, len(good))
        self.assertEqual(trunc.available, 20)
        self.assertEqual(trunc.expected_length, len(full))

        seg = index.segments[0]
        self.assertEqual(seg.frame_count, 3)
        self.assertEqual(seg.playable_end, len(good))
        self.assertEqual(seg.sample_count, 3 * 1024)
        self.assertEqual(seg.duration_seconds, (3 * 1024) / 16000)
        self.assertIsNotNone(seg.truncated)

    def test_truncation_report_inside_concatenated_stream(self):
        id3_a = make_id3v2(b"a")
        id3_b = make_id3v2(b"bb")
        track_a = frames(2, sample_rate_index=4)
        track_b = frames(2, sample_rate_index=8)
        cut = make_adts_frame(payload_size=80, sample_rate_index=8)[:15]
        data = id3_a + track_a + id3_b + track_b + cut

        index = parse_stream(data)
        self.assertEqual(len(index.segments), 2)
        tail = index.segments[1]
        self.assertEqual(tail.truncated.offset, len(id3_a + track_a + id3_b + track_b))
        self.assertEqual(tail.sample_count, 2 * 1024)
        self.assertEqual(tail.playable_end, tail.truncated.offset)

    def test_resync_after_garbage_is_reported_with_offsets(self):
        good = make_adts_frame(payload_size=40)
        data = good + b"\x12\x34\x56" + make_adts_frame(payload_size=40)
        index = parse_stream(data)
        self.assertEqual(len(index.segments), 1)
        self.assertEqual(index.segments[0].frame_count, 2)
        self.assertEqual(len(index.truncated), 1)
        self.assertEqual(index.truncated[0].offset, len(good))
        self.assertEqual(index.truncated[0].available, 3)


class IndexExportTests(unittest.TestCase):
    def test_json_index_round_trip_and_offsets(self):
        id3_tag = make_id3v2(b"meta")
        data = id3_tag + frames(2)
        index = parse_stream(data, source="demo.aac")
        payload = json.loads(index.to_json())
        self.assertEqual(payload["file_size"], len(data))
        self.assertEqual(payload["segment_count"], 1)
        seg = payload["segments"][0]
        self.assertEqual(seg["start"], 0)
        self.assertEqual(seg["end"], len(data))
        self.assertEqual(seg["id3"]["size"], len(id3_tag))
        self.assertEqual(seg["sample_count"], 2048)
        self.assertIsNone(seg["truncated"])

        # Re-reading via exported offsets yields identical bytes.
        exported_start, exported_end = seg["start"], seg["end"]
        self.assertEqual(data[exported_start:exported_end], index.extract_segment_bytes(data, 0))

    def test_empty_stream_produces_no_segments(self):
        index = parse_stream(b"")
        self.assertEqual(index.segments, [])
        self.assertEqual(index.file_size, 0)
        self.assertEqual(json.loads(index.to_json())["segment_count"], 0)

    def test_id3_only_segment_has_zero_playable_duration(self):
        tag = make_id3v2(b"no audio here")
        index = parse_stream(tag)
        self.assertEqual(len(index.segments), 1)
        seg = index.segments[0]
        self.assertEqual(seg.frame_count, 0)
        self.assertEqual(seg.sample_count, 0)
        self.assertEqual(seg.duration_seconds, 0.0)
        self.assertEqual(seg.end, len(tag))


if __name__ == "__main__":
    unittest.main()
