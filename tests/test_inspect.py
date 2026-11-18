import unittest

from bwftool.builder import (
    bext_chunk,
    build_wave,
    data_chunk,
    fmt_chunk,
    ixml_chunk,
    unknown_chunk,
)
from bwftool.inspect import inspect_wave

SAMPLES = bytes((i * 7) % 256 for i in range(24))


def _wave(
    bext_kwargs: dict | None = None,
    ixml_kwargs: dict | None = None,
    include_ixml: bool = True,
    odd_unknown: bool = True,
) -> bytes:
    frames = [
        fmt_chunk(),
        bext_chunk(**(bext_kwargs or {})),
    ]
    if include_ixml:
        frames.append(ixml_chunk(**(ixml_kwargs or {})))
    if odd_unknown:
        frames.append(unknown_chunk(b"junk", b"Q" * 3))
    frames.append(data_chunk(SAMPLES))
    return build_wave(frames)


class InspectionTests(unittest.TestCase):
    def test_consistent_file_has_no_errors(self) -> None:
        result = inspect_wave(_wave())
        severities = {item.severity for item in result.findings}
        self.assertNotIn("error", severities)

    def test_missing_ixml_reports_warning_and_keeps_bext_checks(self) -> None:
        result = inspect_wave(_wave(include_ixml=False))
        codes = {item.code for item in result.findings}
        self.assertIn("NO_IXML", codes)
        self.assertIsNone(result.ixml)
        self.assertIsNotNone(result.bext)

    def test_odd_chunk_padding_is_reported_as_info(self) -> None:
        result = inspect_wave(_wave())
        odd = [
            f
            for f in result.findings
            if f.code == "ODD_CHUNK_PADDING"
            and f.locators[0].chunk_id == "junk"
        ]
        self.assertTrue(odd)
        locator = odd[0].locators[0]
        self.assertEqual(locator.chunk_id, "junk")
        self.assertEqual(locator.length, 1)

    def test_contradictory_time_reference_samples(self) -> None:
        wave = _wave(
            bext_kwargs={"time_reference": 48000},
            ixml_kwargs={"offset_samples": 96000},
        )
        result = inspect_wave(wave)
        mismatch = [
            f for f in result.findings if f.code == "TIME_REFERENCE_MISMATCH"
        ]
        self.assertEqual(len(mismatch), 1)
        locator_specs = [loc.spec() for loc in mismatch[0].locators]
        self.assertTrue(
            any(spec.startswith("bext/TimeReference") for spec in locator_specs)
        )
        self.assertTrue(
            any(
                spec.startswith(
                    "iXML/BWF_START_TIME_OFFSET_SAMPLES"
                )
                for spec in locator_specs
            )
        )
        # 定位偏移必须落在文件范围内且指向真实字段
        for locator in mismatch[0].locators:
            self.assertGreaterEqual(locator.offset, 0)
            self.assertLess(
                locator.offset + locator.length, len(wave)
            )

    def test_contradictory_wallclock_timestamp(self) -> None:
        wave = _wave(
            bext_kwargs={
                "date": "2026-11-19",
                "time_of_day": "10:00:00",
                "time_reference": 0,
            },
            ixml_kwargs={
                "take_timestamp": "2026-11-19T12:00:00Z",
                "offset_samples": 0,
            },
        )
        result = inspect_wave(wave)
        codes = {item.code for item in result.findings}
        self.assertIn("WALLCLOCK_MISMATCH", codes)

    def test_wallclock_consistent_within_one_second(self) -> None:
        wave = _wave(
            bext_kwargs={
                "date": "2026-11-19",
                "time_of_day": "10:00:00",
                "time_reference": 48000,
            },
            ixml_kwargs={
                "take_timestamp": "2026-11-19T10:00:01Z",
                "offset_samples": 48000,
            },
        )
        result = inspect_wave(wave)
        codes = {item.code for item in result.findings}
        self.assertNotIn("WALLCLOCK_MISMATCH", codes)

    def test_sample_type_mismatch_detects_rate_and_bit_depth(self) -> None:
        wave = _wave(
            ixml_kwargs={"sample_rate": 96000, "bit_depth": 16}
        )
        result = inspect_wave(wave)
        codes = {item.code for item in result.findings}
        self.assertIn("SAMPLE_RATE_MISMATCH", codes)
        self.assertIn("BIT_DEPTH_MISMATCH", codes)

    def test_channel_count_mismatch(self) -> None:
        wave = _wave(ixml_kwargs={"channels": 8})
        result = inspect_wave(wave)
        codes = {item.code for item in result.findings}
        self.assertIn("CHANNEL_COUNT_MISMATCH", codes)
        self.assertIn("TRACK_COUNT_MISMATCH", codes)

    def test_float_sample_type_described_and_checked(self) -> None:
        frames = [
            fmt_chunk(format_tag=3, bits_per_sample=32),
            bext_chunk(),
            ixml_chunk(bit_depth=32),
            data_chunk(SAMPLES),
        ]
        result = inspect_wave(build_wave(frames))
        self.assertIn("IEEE float", result.fmt.describe_sample_type())
        severities = {item.severity for item in result.findings}
        self.assertNotIn("error", severities)

    def test_originator_reference_empty_warns(self) -> None:
        wave = _wave(bext_kwargs={"originator_reference": ""})
        result = inspect_wave(wave)
        codes = {item.code for item in result.findings}
        self.assertIn("EMPTY_ORIGINATOR_REFERENCE", codes)

    def test_originator_tape_contradiction(self) -> None:
        wave = _wave(
            bext_kwargs={"originator_reference": "REF-AAA-001"},
            ixml_kwargs={"tape": "TAPE-Z"},
        )
        result = inspect_wave(wave)
        codes = {item.code for item in result.findings}
        self.assertIn("ORIGINATOR_TAPE_MISMATCH", codes)

    def test_scene_take_mismatch_via_description(self) -> None:
        wave = _wave(
            bext_kwargs={"description": "SCENE=SC01 TAKE=9"},
            ixml_kwargs={"scene": "SC01", "take": "3"},
        )
        result = inspect_wave(wave)
        mismatch = [f for f in result.findings if f.code == "TAKE_MISMATCH"]
        self.assertEqual(len(mismatch), 1)
        self.assertEqual(
            [loc.chunk_id for loc in mismatch[0].locators],
            ["bext", "iXML"],
        )

    def test_locator_points_at_real_field_bytes(self) -> None:
        wave = _wave(
            bext_kwargs={"time_reference": 1},
            ixml_kwargs={"offset_samples": 2},
        )
        result = inspect_wave(wave)
        mismatch = next(
            f
            for f in result.findings
            if f.code == "TIME_REFERENCE_MISMATCH"
        )
        bext_locator = next(
            loc for loc in mismatch.locators if loc.chunk_id == "bext"
        )
        value = int.from_bytes(
            wave[
                bext_locator.offset : bext_locator.offset
                + bext_locator.length
            ],
            "little",
        )
        self.assertEqual(value, 1)


if __name__ == "__main__":
    unittest.main()
