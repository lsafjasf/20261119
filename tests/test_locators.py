import unittest

from bwftool.builder import bext_chunk, build_wave, data_chunk, fmt_chunk
from bwftool.bext import BextData
from bwftool.chunks import parse_wave
from bwftool.inspect import inspect_wave


class LocatorPrecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wave = build_wave(
            [
                fmt_chunk(),
                bext_chunk(
                    originator_reference="REF-LOC-1",
                    description="SCENE=SC05 TAKE=8",
                ),
                data_chunk(b"\x10" * 8),
            ]
        )
        self.parsed = parse_wave(self.wave)
        self.bext = BextData.decode(self.parsed.chunk(b"bext"))

    def test_bext_locator_offsets_match_standard_layout(self) -> None:
        desc_offset, desc_len = self.bext.field_locator("Description")
        origin_offset, origin_len = self.bext.field_locator(
            "OriginatorReference"
        )
        # 相对 bext data 的局部偏移遵循 EBU 布局
        local = desc_offset - self.parsed.chunk(b"bext").data_offset
        self.assertEqual(local, 0)
        self.assertEqual(desc_len, 256)
        local = origin_offset - self.parsed.chunk(b"bext").data_offset
        self.assertEqual(local, 288)
        self.assertEqual(origin_len, 32)

    def test_locator_bytes_decode_to_field_value(self) -> None:
        offset, length = self.bext.field_locator("OriginatorReference")
        raw = self.wave[offset : offset + length]
        self.assertEqual(
            raw.split(b"\x00", 1)[0].decode("latin-1"), "REF-LOC-1"
        )

    def test_time_reference_locator_covers_full_uint64(self) -> None:
        offset, length = self.bext.field_locator("TimeReference")
        self.assertEqual(length, 8)
        value = int.from_bytes(
            self.wave[offset : offset + length], "little"
        )
        self.assertEqual(value, self.bext.time_reference)

    def test_ixml_tag_locator_points_at_tag_bytes(self) -> None:
        result = inspect_wave(self.wave)
        # 该夹具没有 iXML，构造一个带 iXML 的最小文件
        from bwftool.builder import ixml_chunk

        wave = build_wave(
            [
                fmt_chunk(),
                bext_chunk(),
                ixml_chunk(scene="SC42"),
                data_chunk(b"\x00" * 4),
            ]
        )
        ixml_result = inspect_wave(wave)
        offset, length = ixml_result.ixml.tag_locator("SCENE")
        segment = wave[offset : offset + length]
        self.assertTrue(segment.startswith(b"<SCENE>"))
        self.assertTrue(segment.endswith(b"</SCENE>"))
        self.assertIn(b"SC42", segment)


if __name__ == "__main__":
    unittest.main()
