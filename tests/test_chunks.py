import unittest

from bwftool.builder import (
    bext_chunk,
    build_wave,
    data_chunk,
    fmt_chunk,
    ixml_chunk,
    unknown_chunk,
)
from bwftool.chunks import WaveFormatError, parse_wave


class ChunkScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.samples = bytes(range(12))
        self.wave = build_wave(
            [
                fmt_chunk(),
                bext_chunk(),
                ixml_chunk(),
                unknown_chunk(b"junk", b"X" * 5),  # 奇数长度
                data_chunk(self.samples),
            ]
        )

    def test_parses_all_chunks_in_order(self) -> None:
        parsed = parse_wave(self.wave)
        ids = [chunk.chunk_id for chunk in parsed.chunks]
        self.assertEqual(
            ids, [b"fmt ", b"bext", b"iXML", b"junk", b"data"]
        )

    def test_odd_length_chunk_has_pad_byte_preserved(self) -> None:
        parsed = parse_wave(self.wave)
        junk = parsed.chunk(b"junk")
        self.assertIsNotNone(junk)
        self.assertEqual(junk.size, 5)
        self.assertEqual(junk.pad, 1)
        self.assertTrue(junk.is_odd)
        self.assertEqual(
            self.wave[junk.data_offset + junk.size], 0x00
        )
        # 下一个 chunk 头紧跟补字节之后
        data = parsed.chunk(b"data")
        self.assertEqual(
            junk.end_offset, data.header_offset
        )

    def test_data_bytes_round_trip_unchanged(self) -> None:
        parsed = parse_wave(self.wave)
        self.assertEqual(parsed.data_chunk.data, self.samples)

    def test_unknown_chunk_payload_preserved(self) -> None:
        parsed = parse_wave(self.wave)
        junk = parsed.chunk(b"junk")
        self.assertEqual(junk.data, b"XXXXX")

    def test_rejects_non_riff(self) -> None:
        with self.assertRaises(WaveFormatError):
            parse_wave(b"XXXX" + b"\x00" * 20)

    def test_rejects_rf64(self) -> None:
        with self.assertRaises(WaveFormatError):
            parse_wave(b"RF64" + b"\x00" * 20)

    def test_truncated_chunk_raises(self) -> None:
        truncated = self.wave[:-4]
        with self.assertRaises(WaveFormatError):
            parse_wave(truncated)

    def test_fmt_extensible_decode(self) -> None:
        # WAVE_FORMAT_EXTENSIBLE: 40 字节，子格式 GUID 首两字节为 PCM
        body = (
            (0xFFFE).to_bytes(2, "little")
            + (2).to_bytes(2, "little")
            + (48000).to_bytes(4, "little")
            + (288000).to_bytes(4, "little")
            + (6).to_bytes(2, "little")
            + (24).to_bytes(2, "little")
            + (22).to_bytes(2, "little")
            + (20).to_bytes(2, "little")
            + (3).to_bytes(4, "little")
            + (1).to_bytes(2, "little")
            + b"\x00" * 14
        )
        frame = b"fmt " + len(body).to_bytes(4, "little") + body
        parsed = parse_wave(build_wave([frame, data_chunk(b"\x00" * 6)]))
        fmt = parsed.decode_fmt()
        self.assertEqual(fmt.effective_format_tag, 1)
        self.assertEqual(fmt.valid_bits_per_sample, 20)


if __name__ == "__main__":
    unittest.main()
