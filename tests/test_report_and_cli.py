import json
import tempfile
import unittest
from pathlib import Path

from bwftool.builder import (
    bext_chunk,
    build_wave,
    data_chunk,
    fmt_chunk,
    ixml_chunk,
    unknown_chunk,
)
from bwftool.chunks import parse_wave
from bwftool.cli import main
from bwftool.report import render_html, render_text
from bwftool.inspect import inspect_wave

SAMPLES = bytes((i * 11 + 1) % 256 for i in range(18))


def _contradictory_wave() -> bytes:
    return build_wave(
        [
            fmt_chunk(),
            bext_chunk(time_reference=48000, description="TAKE=1"),
            ixml_chunk(offset_samples=99999, take="2"),
            unknown_chunk(b"junk", b"K" * 3),
            data_chunk(SAMPLES),
        ]
    )


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.result = inspect_wave(_contradictory_wave())

    def test_text_report_contains_locator_specs(self) -> None:
        text = render_text(self.result, "demo.wav")
        self.assertIn("TIME_REFERENCE_MISMATCH", text)
        self.assertIn("bext/TimeReference@0x", text)

    def test_html_report_has_anchor_and_target(self) -> None:
        markup = render_html(self.result, "demo.wav")
        mismatch = next(
            f
            for f in self.result.findings
            if f.code == "TIME_REFERENCE_MISMATCH"
        )
        anchor = f'#off-{mismatch.locators[0].offset}'
        self.assertIn(anchor, markup)
        self.assertIn(f'id="off-{mismatch.locators[0].offset}"', markup)


class CliTests(unittest.TestCase):
    def test_inspect_json_and_edit_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "in.wav"
            source.write_bytes(_contradictory_wave())
            json_path = tmp_path / "report.json"
            html_path = tmp_path / "report.html"
            output = tmp_path / "out.wav"

            code = main(
                [
                    "inspect",
                    str(source),
                    "--json",
                    str(json_path),
                    "--html",
                    str(html_path),
                ]
            )
            self.assertEqual(code, 1)  # 存在 error 级问题
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            codes = {item["code"] for item in payload["findings"]}
            self.assertIn("TIME_REFERENCE_MISMATCH", codes)
            self.assertIn("<html", html_path.read_text(encoding="utf-8"))

            code = main(
                [
                    "edit",
                    str(source),
                    str(output),
                    "--set",
                    "iXML.TAKE=8",
                ]
            )
            self.assertEqual(code, 0)
            before = parse_wave(source.read_bytes()).data_chunk.data
            after = parse_wave(output.read_bytes()).data_chunk.data
            self.assertEqual(before, after)

            code = main(["fields", str(source)])
            self.assertEqual(code, 0)

    def test_inspect_clean_file_exits_zero(self) -> None:
        clean = build_wave(
            [fmt_chunk(), bext_chunk(), ixml_chunk(), data_chunk(SAMPLES)]
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "clean.wav"
            source.write_bytes(clean)
            self.assertEqual(main(["inspect", str(source)]), 0)


if __name__ == "__main__":
    unittest.main()
