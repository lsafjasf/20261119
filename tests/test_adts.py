import unittest

from adts_parser import (
    ADTS_HEADER_SIZE_NO_CRC,
    ADTS_HEADER_SIZE_WITH_CRC,
    ADTSParseError,
    SAMPLE_RATES,
    parse_adts_header,
    parse_id3v2_header,
)
from samples.make_samples import make_adts_frame, make_id3v2


class HeaderTests(unittest.TestCase):
    def test_no_crc_header_is_seven_bytes(self):
        frame = make_adts_frame(payload_size=100, protection_absent=1)
        header = parse_adts_header(frame)
        self.assertFalse(header.has_crc)
        self.assertEqual(header.header_size, ADTS_HEADER_SIZE_NO_CRC)
        self.assertEqual(header.frame_length, 107)
        self.assertEqual(header.sample_rate, 44100)
        self.assertEqual(header.sample_rate_index, 4)
        self.assertEqual(header.profile, 1)
        self.assertEqual(header.audio_object_type, 2)
        self.assertEqual(header.channel_config, 2)
        self.assertEqual(header.sample_count, 1024)

    def test_crc_header_is_nine_bytes(self):
        frame = make_adts_frame(payload_size=100, protection_absent=0)
        header = parse_adts_header(frame)
        self.assertTrue(header.has_crc)
        self.assertEqual(header.header_size, ADTS_HEADER_SIZE_WITH_CRC)
        self.assertEqual(header.frame_length, 109)
        self.assertEqual(frame[:9], frame[:header.header_size])

    def test_all_sample_rate_indices(self):
        for index, rate in SAMPLE_RATES.items():
            frame = make_adts_frame(payload_size=10, sample_rate_index=index)
            header = parse_adts_header(frame)
            self.assertEqual(header.sample_rate_index, index)
            self.assertEqual(header.sample_rate, rate)

    def test_bad_sync_word_rejected(self):
        frame = bytearray(make_adts_frame(payload_size=10))
        frame[0] = 0x00
        with self.assertRaisesRegex(ADTSParseError, "sync word"):
            parse_adts_header(bytes(frame))

    def test_reserved_sample_rate_index_rejected(self):
        frame = bytearray(make_adts_frame(payload_size=10))
        # sri occupies b2 bits 5..2; set all four to 1 (reserved index 0xF).
        frame[2] = (frame[2] & 0xC1) | 0x3C
        with self.assertRaisesRegex(ADTSParseError, "sampling_frequency_index"):
            parse_adts_header(bytes(frame))

    def test_truncated_header_rejected(self):
        frame = make_adts_frame(payload_size=10)
        with self.assertRaisesRegex(ADTSParseError, "truncated header"):
            parse_adts_header(frame[:5])

    def test_crc_truncation_requires_nine_bytes(self):
        frame = make_adts_frame(payload_size=10, protection_absent=0)
        with self.assertRaisesRegex(ADTSParseError, "CRC-protected"):
            parse_adts_header(frame[:7])

    def test_round_trip_every_field(self):
        frame = make_adts_frame(
            payload_size=233,
            profile=2,
            sample_rate_index=7,  # 22050 Hz
            channel_config=1,
            mpeg_version=1,
            protection_absent=0,
        )
        header = parse_adts_header(frame)
        self.assertEqual(header.frame_length, 233 + 9)
        self.assertEqual(header.mpeg_version, 1)
        self.assertEqual(header.profile, 2)
        self.assertEqual(header.audio_object_type, 3)
        self.assertEqual(header.sample_rate, 22050)
        self.assertEqual(header.channel_config, 1)
        self.assertEqual(header.num_raw_data_blocks, 1)


class ID3Tests(unittest.TestCase):
    def test_id3v2_size_without_footer(self):
        tag = make_id3v2(b"x" * 40)
        parsed = parse_id3v2_header(tag)
        self.assertEqual(parsed.body_size, 40)
        self.assertEqual(parsed.total_size, 50)
        self.assertFalse(parsed.has_footer)

    def test_id3v2_footer_flag_extends_size(self):
        tag = make_id3v2(b"y" * 20, flags=0x10) + b"3DI" + b"\x00" * 7
        parsed = parse_id3v2_header(tag)
        self.assertTrue(parsed.has_footer)
        self.assertEqual(parsed.total_size, 40)


if __name__ == "__main__":
    unittest.main()
