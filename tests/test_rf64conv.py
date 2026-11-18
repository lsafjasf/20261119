from __future__ import annotations

import json
import os
import struct
import tempfile
import unittest

from rf64conv import (
    DS64_ID,
    UINT32_MAX,
    WavError,
    convert,
    parse_wave,
)


def pcm_format() -> bytes:
    return struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)


def chunk(chunk_id: bytes, payload: bytes, pad: bytes = b"\0") -> bytes:
    padding = pad if len(payload) & 1 else b""
    return chunk_id + struct.pack("<I", len(payload)) + payload + padding


def make_riff(path: str) -> bytes:
    fmt = pcm_format()
    body = (
        b"WAVE"
        + chunk(b"fmt ", fmt)
        + chunk(b"data", bytes(range(9)), b"\xaa")
        + chunk(b"JUNK", b"abc", b"\x55")
        + chunk(b"zzzz", b"opaque payload")
    )
    content = b"RIFF" + struct.pack("<I", len(body)) + body
    with open(path, "wb") as handle:
        handle.write(content)
    return content


def make_rf64_with_table(path: str) -> None:
    fmt = pcm_format()
    table = [(b"BIG!", 7)]
    ds64_payload = (
        struct.pack("<QQQI", 0, 4, 2, len(table))
        + b"".join(chunk_id + struct.pack("<Q", size) for chunk_id, size in table)
    )
    chunks = (
        b"BIG!"
        + struct.pack("<I", UINT32_MAX)
        + b"1234567"
        + b"\xcc"
        + chunk(b"fmt ", fmt)
        + chunk(b"data", b"\x01\x02\x03\x04")
    )
    total = 12 + 8 + len(ds64_payload) + len(chunks)
    ds64_payload = (
        struct.pack("<QQQI", total - 8, 4, 2, len(table))
        + b"".join(chunk_id + struct.pack("<Q", size) for chunk_id, size in table)
    )
    content = (
        b"RF64"
        + struct.pack("<I", UINT32_MAX)
        + b"WAVE"
        + b"ds64"
        + struct.pack("<I", len(ds64_payload))
        + ds64_payload
        + chunks
    )
    assert len(content) == total
    with open(path, "wb") as handle:
        handle.write(content)


def make_large_sparse_rf64(path: str) -> tuple[int, int, bytes, bytes]:
    data_size = (1 << 32) + 8192
    fmt = struct.pack("<HHIIHH", 1, 2, 48000, 192000, 4, 16)
    ds64_payload = struct.pack("<QQQI", 0, data_size, data_size // 4, 0)
    total = 12 + 8 + len(ds64_payload) + 8 + len(fmt) + 8 + data_size
    ds64_payload = struct.pack("<QQQI", total - 8, data_size, data_size // 4, 0)
    header = (
        b"RF64"
        + struct.pack("<I", UINT32_MAX)
        + b"WAVE"
        + b"ds64"
        + struct.pack("<I", len(ds64_payload))
        + ds64_payload
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", UINT32_MAX)
    )
    first = b"FIRST!" * 8
    last = b"LAST!!" * 8
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, header)
        os.ftruncate(fd, total)
        os.pwrite(fd, first, len(header))
        os.pwrite(fd, last, total - len(last))
    finally:
        os.close(fd)
    return total, len(header), first, last


def read_payload(path: str, chunk_index: int) -> bytes:
    info = parse_wave(path)
    chunk_info = info.chunks[chunk_index]
    with open(path, "rb") as handle:
        handle.seek(chunk_info.data_offset)
        return handle.read(chunk_info.size)


class ConversionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="rf64conv-test-")
        self.directory = self.temporary.name

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def path(self, name: str) -> str:
        return os.path.join(self.directory, name)

    def test_round_trip_preserves_unknown_chunks_and_odd_padding(self) -> None:
        source_path = self.path("source.wav")
        rf64_path = self.path("converted.rf64")
        restored_path = self.path("restored.wav")
        original = make_riff(source_path)

        manifest, _ = convert(
            source_path,
            rf64_path,
            requested_format="rf64",
            manifest_path=self.path("to-rf64.json"),
        )
        rf64_info = parse_wave(rf64_path)
        self.assertEqual(rf64_info.container, "RF64")
        self.assertEqual(
            [item.chunk_id for item in rf64_info.chunks],
            [DS64_ID, b"fmt ", b"data", b"JUNK", b"zzzz"],
        )
        with open(rf64_path, "rb") as handle:
            content = handle.read()
        self.assertIn(bytes(range(9)) + b"\xaa", content)
        self.assertIn(b"abc" + b"\x55", content)
        self.assertIn(b"opaque payload", content)

        manifest_after = manifest["after"]
        manifest_before = manifest["before"]
        assert isinstance(manifest_after, dict)
        assert isinstance(manifest_before, dict)
        self.assertNotEqual(
            [item["offset"] for item in manifest_before["chunks"]],
            [item["offset"] for item in manifest_after["chunks"]],
        )
        self.assertTrue(manifest["verification"]["all_equal"])

        convert(
            rf64_path,
            restored_path,
            requested_format="riff",
            manifest_path=self.path("to-riff.json"),
        )
        with open(restored_path, "rb") as handle:
            restored = handle.read()
        self.assertEqual(restored, original)
        self.assertEqual(parse_wave(restored_path).container, "RIFF")

    def test_ds64_table_resolves_a_64_bit_unknown_chunk_size(self) -> None:
        source_path = self.path("table.rf64")
        output_path = self.path("table.out.rf64")
        make_rf64_with_table(source_path)
        info = parse_wave(source_path)
        big = next(item for item in info.chunks if item.chunk_id == b"BIG!")
        self.assertEqual(big.size_field, UINT32_MAX)
        self.assertEqual(big.size, 7)
        self.assertEqual(big.size_source, "ds64:table")
        self.assertEqual(big.pad_size, 1)
        self.assertEqual(read_payload(source_path, info.chunks.index(big)), b"1234567")

        convert(
            source_path,
            output_path,
            requested_format="rf64",
            manifest_path=self.path("table.json"),
        )
        output_info = parse_wave(output_path)
        output_big = next(
            item for item in output_info.chunks if item.chunk_id == b"BIG!"
        )
        self.assertEqual(output_big.size, 7)
        self.assertEqual(
            read_payload(output_path, output_info.chunks.index(output_big)),
            b"1234567",
        )

    def test_truncated_declarations_do_not_touch_existing_target(self) -> None:
        cases = {
            "case-riff.wav": (
                b"RIFF"
                + struct.pack("<I", 1000)
                + b"WAVE"
                + b"data"
                + struct.pack("<I", 100)
                + b"abc"
            ),
            "case-rf64.rf64": (
                b"RF64"
                + struct.pack("<I", UINT32_MAX)
                + b"WAVE"
                + b"ds64"
                + struct.pack("<I", 28)
                + struct.pack("<QQQI", 5000, 4, 2, 0)
                + b"data"
                + struct.pack("<I", 4)
                + b"\0\0\0\0"
            ),
        }
        for name, content in cases.items():
            with self.subTest(name=name):
                source_path = self.path(name)
                target_path = self.path(name + ".out")
                sentinel = b"existing-target-must-survive"
                with open(source_path, "wb") as handle:
                    handle.write(content)
                with open(target_path, "wb") as handle:
                    handle.write(sentinel)
                with self.assertRaises(WavError):
                    convert(
                        source_path,
                        target_path,
                        requested_format="rf64",
                        manifest_path=self.path(name + ".json"),
                    )
                with open(target_path, "rb") as handle:
                    self.assertEqual(handle.read(), sentinel)
                self.assertFalse(os.path.exists(self.path(name + ".json")))

    def test_sparse_input_over_4_gib_is_copied_without_materializing_holes(
        self,
    ) -> None:
        source_path = self.path("large.rf64")
        output_path = self.path("large.out.rf64")
        total, data_offset, first, last = make_large_sparse_rf64(source_path)
        self.assertGreater(total, 1 << 32)
        input_stat = os.stat(source_path)
        self.assertLess(input_stat.st_blocks * 512, total // 100)

        manifest, _ = convert(
            source_path,
            output_path,
            requested_format="rf64",
            manifest_path=self.path("large.json"),
            verify_bytes=64,
        )
        output_stat = os.stat(output_path)
        self.assertEqual(output_stat.st_size, total)
        self.assertLess(output_stat.st_blocks * 512, total // 100)
        with open(output_path, "rb") as handle:
            handle.seek(data_offset)
            self.assertEqual(handle.read(len(first)), first)
            handle.seek(total - len(last))
            self.assertEqual(handle.read(), last)
        self.assertTrue(manifest["verification"]["all_equal"])
        output_chunks = manifest["after"]["chunks"]
        self.assertEqual(output_chunks[-1]["id"], "data")
        self.assertEqual(output_chunks[-1]["length"], total - data_offset)

        blocked_target = self.path("blocked-riff.wav")
        with open(blocked_target, "wb") as handle:
            handle.write(b"do-not-overwrite")
        with self.assertRaises(WavError):
            convert(
                source_path,
                blocked_target,
                requested_format="riff",
                manifest_path=self.path("blocked.json"),
            )
        with open(blocked_target, "rb") as handle:
            self.assertEqual(handle.read(), b"do-not-overwrite")


if __name__ == "__main__":
    unittest.main()
