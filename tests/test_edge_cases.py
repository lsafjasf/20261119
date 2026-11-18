import unittest

from bwftool.builder import (
    bext_chunk,
    build_wave,
    data_chunk,
    fmt_chunk,
)
from bwftool.chunks import parse_wave
from bwftool.editor import EditError, apply_edits
from bwftool.inspect import inspect_wave

SAMPLES = b"\x01\x02\x03\x04\x05\x06"


class EdgeCaseTests(unittest.TestCase):
    def test_odd_bext_coding_history_round_trip_with_pad(self) -> None:
        # 602 定长 + 5 字节 CodingHistory -> bext 数据为奇数长度
        wave = build_wave(
            [
                fmt_chunk(),
                bext_chunk(coding_history="ABCDE"),
                data_chunk(SAMPLES),
            ]
        )
        parsed = parse_wave(wave)
        bext = parsed.chunk(b"bext")
        self.assertEqual(bext.size, 607)
        self.assertEqual(bext.pad, 1)

        exported = apply_edits(
            wave, {"bext.CodingHistory": "ABCDEFX"}  # 7 字节，仍为奇数
        )
        exported_parsed = parse_wave(exported)
        self.assertEqual(exported_parsed.chunk(b"bext").pad, 1)
        self.assertEqual(
            exported_parsed.data_chunk.data, SAMPLES
        )
        result = inspect_wave(exported)
        self.assertEqual(result.bext.coding_history, "ABCDEFX")

    def test_ixml_with_namespace_is_parsed_and_editable(self) -> None:
        payload = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<BWFXML xmlns="http://example.com/ixml">'
            b"<SCENE>SC07</SCENE><TAKE>2</TAKE>"
            b"<SAMPLE_RATE>48000.0</SAMPLE_RATE>"
            b"<BIT_DEPTH>24</BIT_DEPTH>"
            b"<FILE_SAMPLE_WIDTH>24</FILE_SAMPLE_WIDTH>"
            b"<FILE_CHANNEL_COUNT>2</FILE_CHANNEL_COUNT>"
            b"<TRACK_COUNT>2</TRACK_COUNT>"
            b"</BWFXML>"
        )
        frame = b"iXML" + len(payload).to_bytes(4, "little") + payload
        if len(payload) % 2 == 1:
            frame += b"\x00"
        wave = build_wave([fmt_chunk(), bext_chunk(), frame, data_chunk(SAMPLES)])
        result = inspect_wave(wave)
        self.assertEqual(result.ixml.scene, "SC07")
        exported = apply_edits(wave, {"iXML.SCENE": "SC08"})
        self.assertEqual(inspect_wave(exported).ixml.scene, "SC08")
        self.assertEqual(parse_wave(exported).data_chunk.data, SAMPLES)

    def test_non_editable_ixml_tag_rejected(self) -> None:
        wave = build_wave(
            [fmt_chunk(), bext_chunk(), data_chunk(SAMPLES)]
        )
        # 无 iXML 时 iXML 编辑应报错
        with self.assertRaises(EditError):
            apply_edits(wave, {"iXML.SAMPLE_RATE": "96000"})

    def test_malformed_ixml_raises_value_error(self) -> None:
        payload = b"<BWFXML><SCENE>broken"
        frame = b"iXML" + len(payload).to_bytes(4, "little") + payload
        if len(payload) % 2 == 1:
            frame += b"\x00"
        wave = build_wave([fmt_chunk(), bext_chunk(), frame, data_chunk(SAMPLES)])
        with self.assertRaises(ValueError):
            inspect_wave(wave)


if __name__ == "__main__":
    unittest.main()
