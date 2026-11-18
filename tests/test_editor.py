import unittest

from bwftool.builder import (
    bext_chunk,
    build_wave,
    data_chunk,
    fmt_chunk,
    ixml_chunk,
    unknown_chunk,
)
from bwftool.chunks import parse_wave
from bwftool.editor import EditError, apply_edits, list_editable_fields
from bwftool.inspect import inspect_wave

SAMPLES = bytes((i * 13 + 5) % 256 for i in range(36))


def _wave(description: str = "SCENE=SC01 TAKE=3") -> bytes:
    return build_wave(
        [
            fmt_chunk(),
            bext_chunk(description=description),
            ixml_chunk(),
            unknown_chunk(b"junk", b"Z" * 7),  # 奇数长度未知 chunk
            data_chunk(SAMPLES),
        ]
    )


class EditorTests(unittest.TestCase):
    def test_edit_ixml_take_keeps_data_bytes_identical(self) -> None:
        original = _wave()
        exported = apply_edits(original, {"iXML.TAKE": "42"})

        original_parsed = parse_wave(original)
        exported_parsed = parse_wave(exported)
        self.assertEqual(
            original_parsed.data_chunk.data,
            exported_parsed.data_chunk.data,
        )
        self.assertEqual(
            original_parsed.data_chunk.size,
            exported_parsed.data_chunk.size,
        )

    def test_unknown_chunk_preserved_byte_for_byte(self) -> None:
        original = _wave()
        exported = apply_edits(original, {"iXML.NOTE": "updated note"})
        before = parse_wave(original).chunk(b"junk")
        after = parse_wave(exported).chunk(b"junk")
        self.assertEqual(before.data, after.data)
        self.assertEqual(before.pad, after.pad)
        self.assertEqual(before.size, after.size)

    def test_chunk_order_preserved(self) -> None:
        exported = apply_edits(_wave(), {"iXML.TAKE": "9"})
        ids = [chunk.chunk_id for chunk in parse_wave(exported).chunks]
        self.assertEqual(
            ids, [b"fmt ", b"bext", b"iXML", b"junk", b"data"]
        )

    def test_edited_take_visible_in_reparsed_ixml(self) -> None:
        exported = apply_edits(_wave(), {"iXML.TAKE": "42"})
        result = inspect_wave(exported)
        self.assertEqual(result.ixml.take, "42")

    def test_bext_description_edit_and_length_guard(self) -> None:
        exported = apply_edits(
            _wave(), {"bext.Description": "SCENE=SC02 TAKE=1"}
        )
        result = inspect_wave(exported)
        self.assertEqual(result.bext.description, "SCENE=SC02 TAKE=1")
        with self.assertRaises(EditError):
            apply_edits(_wave(), {"bext.Originator": "X" * 40})

    def test_time_reference_and_fmt_are_readonly(self) -> None:
        with self.assertRaises(EditError):
            apply_edits(_wave(), {"bext.TimeReference": "123"})
        with self.assertRaises(EditError):
            apply_edits(_wave(), {"data.something": "x"})
        with self.assertRaises(EditError):
            apply_edits(_wave(), {"fmt.sample_rate": "96000"})

    def test_unknown_chunk_editing_rejected(self) -> None:
        with self.assertRaises(EditError):
            apply_edits(_wave(), {"junk.foo": "bar"})

    def test_editable_catalog_excludes_readonly_fields(self) -> None:
        result = inspect_wave(_wave())
        keys = {field.key for field in list_editable_fields(result)}
        self.assertIn("iXML.TAKE", keys)
        self.assertIn("bext.Description", keys)
        self.assertNotIn("bext.TimeReference", keys)
        self.assertNotIn("data.samples", keys)

    def test_riff_size_header_updated_correctly(self) -> None:
        exported = apply_edits(_wave(), {"iXML.TAKE": "100000"})
        parsed = parse_wave(exported)
        self.assertEqual(
            parsed.riff_declared_size, parsed.riff_actual_size
        )

    def test_edit_without_ixml_chunk_reports_error(self) -> None:
        wave = build_wave(
            [fmt_chunk(), bext_chunk(), data_chunk(SAMPLES)]
        )
        with self.assertRaises(EditError):
            apply_edits(wave, {"iXML.TAKE": "1"})

    def test_full_byte_integrity_of_audio_after_multiple_edits(self) -> None:
        original = _wave()
        exported = apply_edits(
            original,
            {
                "iXML.SCENE": "SC99",
                "iXML.TAKE": "77",
                "bext.Originator": "Rig2",
            },
        )
        original_data = parse_wave(original).data_chunk
        exported_data = parse_wave(exported).data_chunk
        # 音频样本逐字节相等
        self.assertEqual(original_data.data, exported_data.data)
        # 且 data chunk 之外的 fmt 也逐字节保留
        self.assertEqual(
            parse_wave(original).chunk(b"fmt ").data,
            parse_wave(exported).chunk(b"fmt ").data,
        )


if __name__ == "__main__":
    unittest.main()
