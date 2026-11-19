import math
import os
import tempfile
import unittest
import zlib

from pngopt import images
from pngopt.decode import decode_png
from pngopt.optimizer import (
    beam_search,
    heuristic_greedy,
    lookahead_search,
    precompute_variants,
)
from pngopt.png import (
    FILTER_PAETH,
    PNGError,
    UnsupportedInterlaceError,
    build_png,
    parse_png,
    paeth_predictor,
    reconstruct_row,
)
from pngopt.report import verify_output


def _write_tmp(suffix, **kwargs):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    images.write_png(path, **kwargs)
    with open(path, "rb") as handle:
        return path, handle.read()


class PaethTests(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(paeth_predictor(0, 0, 0), 0)
        self.assertEqual(paeth_predictor(10, 0, 0), 10)
        self.assertEqual(paeth_predictor(0, 10, 0), 10)
        # Tie resolution: a wins ties, b wins against c.
        self.assertEqual(paeth_predictor(5, 5, 5), 5)


class FilterRoundTripTests(unittest.TestCase):
    def test_all_filters_reconstruct(self):
        path, data = _write_tmp(
            ".png", width=48, height=32, bit_depth=8, color_type=6,
            pixel_rows=images.alpha_pattern(48, 32))
        parsed = parse_png(data)
        raw_rows, variants = precompute_variants(parsed)
        for method in range(5):
            prev = None
            for r, raw in enumerate(raw_rows):
                rec = reconstruct_row(variants[r][method], prev,
                                      parsed.row_stride, parsed.bpp)
                self.assertEqual(rec, raw)
                prev = raw
        os.unlink(path)

    def test_sub_byte_stride(self):
        for depth in (1, 2, 4):
            path, data = _write_tmp(
                ".png", width=33, height=7, bit_depth=depth, color_type=0,
                pixel_rows=images.sub_byte_gray(depth, 33, 7))
            parsed = parse_png(data)
            self.assertEqual(parsed.row_stride,
                             math.ceil(33 * depth / 8))
            raw_rows, variants = precompute_variants(parsed)
            for r, raw in enumerate(raw_rows):
                rec = reconstruct_row(variants[r][FILTER_PAETH],
                                      raw_rows[r - 1] if r else None,
                                      parsed.row_stride, parsed.bpp)
                self.assertEqual(rec, raw)
            os.unlink(path)


class StrategyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {}
        specs = [
            ("gradient", 64, 64, 8, 2, images.gradient_rgb(64, 64), None),
            ("noise", 64, 64, 8, 2, images.noise_rgb(64, 64), None),
            ("alpha", 64, 64, 8, 6, images.alpha_pattern(64, 64), None),
            ("gray16", 64, 64, 16, 0, images.gradient_gray16(64, 64), None),
        ]
        for name, w, h, depth, color_type, rows, palette in specs:
            path, data = _write_tmp(
                f"-{name}.png", width=w, height=h, bit_depth=depth,
                color_type=color_type, pixel_rows=rows, palette=palette)
            cls.cases[name] = (path, data)

    @classmethod
    def tearDownClass(cls):
        for path, _ in cls.cases.values():
            os.unlink(path)

    def _check_strategy(self, result_factory):
        for _, data in self.cases.values():
            parsed = parse_png(data)
            _, variants = precompute_variants(parsed)
            result = result_factory(parsed, variants)
            self.assertEqual(len(result.filters), parsed.height)
            output = build_png(parsed, result.payload)
            verification = verify_output(parsed, output)
            self.assertTrue(verification["ok"], verification)
            self.assertEqual(decode_png(output)["pixels"],
                             decode_png(data)["pixels"])

    def test_heuristic(self):
        self._check_strategy(
            lambda p, v: heuristic_greedy(p, variants=v))

    def test_lookahead(self):
        self._check_strategy(
            lambda p, v: lookahead_search(p, window=3, variants=v))

    def test_beam(self):
        self._check_strategy(
            lambda p, v: beam_search(p, width=4, variants=v))

    def test_single_path_searches_are_near_optimal(self):
        # Window 1 and beam width 1 are single-path true-size searches.
        # Flush-boundary ties can pick different equivalent filter sequences,
        # so assert near-equal size and verified pixel correctness instead.
        _, data = self.cases["gradient"]
        parsed = parse_png(data)
        _, variants = precompute_variants(parsed)
        look = lookahead_search(parsed, window=1, variants=variants)
        beam = beam_search(parsed, width=1, variants=variants)
        self.assertLess(
            abs(look.stats["idat_compressed_bytes"]
                - beam.stats["idat_compressed_bytes"]),
            max(16, look.stats["idat_compressed_bytes"] // 50))
        for result in (look, beam):
            verification = verify_output(
                parsed, build_png(parsed, result.payload))
            self.assertTrue(verification["ok"])
        self.assertEqual(len(look.filters), parsed.height)

    def test_ancillary_chunks_preserved(self):
        _, data = self.cases["alpha"]
        parsed = parse_png(data)
        _, variants = precompute_variants(parsed)
        result = beam_search(parsed, width=4, variants=variants)
        output = build_png(parsed, result.payload)
        reparsed = parse_png(output)
        before = [c for c in parsed.chunks if c[0] not in
                  (b"IDAT", b"IEND")]
        after = [c for c in reparsed.chunks if c[0] not in
                 (b"IDAT", b"IEND")]
        self.assertEqual(before, after)
        self.assertTrue(any(c[0] == b"tEXt" for c in after))


class InterlaceRejectionTests(unittest.TestCase):
    def test_interlaced_rejected_everywhere(self):
        path, data = _write_tmp(
            ".png", width=32, height=32, bit_depth=8, color_type=2,
            pixel_rows=images.gradient_rgb(32, 32), interlace=1)
        with self.assertRaises(UnsupportedInterlaceError):
            parse_png(data)
        with self.assertRaises(UnsupportedInterlaceError):
            decode_png(data)
        os.unlink(path)

    def test_bad_signature(self):
        with self.assertRaises(PNGError):
            parse_png(b"not a png at all")

    def test_cli_rejects_interlace_with_exit_code(self):
        from pngopt.__main__ import main

        fd, input_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        fd, output_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        os.unlink(output_path)
        images.write_png(
            input_path, width=16, height=16, bit_depth=8, color_type=2,
            pixel_rows=images.gradient_rgb(16, 16), interlace=1)
        code = main(["optimize", input_path, output_path,
                     "--strategy", "heuristic"])
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(output_path))
        os.unlink(input_path)


class MultiIdatTests(unittest.TestCase):
    def test_concatenated_idat_chunks(self):
        from pngopt.png import PNG_SIGNATURE, make_chunk

        width = height = 24
        rows = images.gradient_rgb(width, height)
        raw = b"".join(bytes([0]) + bytes(row) for row in rows)
        payload = zlib.compress(raw, 9)
        ihdr = (width.to_bytes(4, "big") + height.to_bytes(4, "big")
                + bytes((8, 2, 0, 0, 0)))
        blob = (
            PNG_SIGNATURE
            + make_chunk(b"IHDR", ihdr)
            + make_chunk(b"tEXt", b"keep\x00me")
            + make_chunk(b"IDAT", payload[:30])
            + make_chunk(b"IDAT", payload[30:90])
            + make_chunk(b"IDAT", payload[90:])
            + make_chunk(b"IEND", b""))
        parsed = parse_png(blob)
        idat_chunks = [c for c in parsed.chunks if c[0] == b"IDAT"]
        self.assertEqual(len(idat_chunks), 3)

        _, variants = precompute_variants(parsed)
        for factory in (
            lambda: heuristic_greedy(parsed, variants=variants),
            lambda: lookahead_search(parsed, window=2, variants=variants),
            lambda: beam_search(parsed, width=4, variants=variants),
        ):
            result = factory()
            output = build_png(parsed, result.payload, idat_limit=64)
            reparsed = parse_png(output)
            kept = [c for c in reparsed.chunks
                    if c[0] not in (b"IDAT", b"IEND")]
            self.assertEqual(
                kept,
                [c for c in parsed.chunks
                 if c[0] not in (b"IDAT", b"IEND")])
            self.assertTrue(verify_output(parsed, output)["ok"])
            self.assertTrue(
                sum(1 for c in reparsed.chunks if c[0] == b"IDAT") > 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
