from __future__ import annotations

import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from timeline_markers import (
    MarkerError,
    Timeline,
    dumps_edit_list,
    export_edit_list,
    load_edit_list,
    read_edit_list,
    write_edit_list,
)

SECOND = 1_000_000


def make_timeline() -> Timeline:
    timeline = Timeline()
    timeline.add_clip(
        "asset-a",
        timeline_start=10 * SECOND,
        source_in=100 * SECOND,
        source_out=110 * SECOND,
        speed=Fraction(3, 2),
        clip_id="clip-a",
    )
    timeline.add_media_marker(
        "clip-a",
        102 * SECOND,
        marker_id="media-1",
        color="#ff0000",
        note="media note",
        event_type="sync",
    )
    timeline.add_clip_anchor_marker(
        "clip-a",
        2 * SECOND,
        marker_id="clip-1",
        color="cyan",
        note="clip note",
        event_type="cue",
    )
    timeline.add_timeline_marker(
        25 * SECOND,
        marker_id="global-1",
        color="white",
        note="global note",
        event_type="chapter",
    )
    return timeline


class EditListExportTests(unittest.TestCase):
    def test_every_marker_has_one_entry_and_one_media_time(self) -> None:
        timeline = make_timeline()
        document = export_edit_list(timeline)
        markers = document["markers"]
        ids = [marker["id"] for marker in markers]

        self.assertEqual(sorted(ids), ["clip-1", "global-1", "media-1"])
        self.assertEqual(len(ids), len(set(ids)))
        expected_media = {
            "media-1": 102 * SECOND,
            "clip-1": timeline.marker_media_tick("clip-1"),
            "global-1": None,
        }
        for marker in markers:
            self.assertEqual(marker["media_tick"], expected_media[marker["id"]])

    def test_export_preserves_metadata_and_exact_speed(self) -> None:
        document = export_edit_list(make_timeline())
        clip = document["clips"][0]
        marker = document["markers"][0]

        self.assertEqual(clip["speed"], {"numerator": 3, "denominator": 2})
        self.assertEqual(marker["color"], "#ff0000")
        self.assertEqual(marker["note"], "media note")
        self.assertEqual(marker["event_type"], "sync")

    def test_json_dump_round_trip_is_byte_stable(self) -> None:
        timeline = make_timeline()
        first = dumps_edit_list(timeline)
        second = dumps_edit_list(load_edit_list(first))
        self.assertEqual(first, second)

    def test_hidden_clip_markers_survive_round_trip(self) -> None:
        timeline = make_timeline()
        timeline.trim_clip("clip-a", source_in=103 * SECOND)
        document = export_edit_list(timeline)
        media_entry = next(m for m in document["markers"] if m["id"] == "media-1")
        self.assertFalse(media_entry["visible"])

        loaded = load_edit_list(document)
        self.assertFalse(loaded.marker_visible("media-1"))
        loaded.trim_clip("clip-a", source_in=101 * SECOND)
        self.assertTrue(loaded.marker_visible("media-1"))


class EditListImportTests(unittest.TestCase):
    def test_import_ignores_derived_time_noise(self) -> None:
        timeline = make_timeline()
        original = dumps_edit_list(timeline)
        document = json.loads(original)
        for marker in document["markers"]:
            marker["media_tick"] = 1
            marker["timeline_tick"] = 2
            marker["visible"] = not marker["visible"]

        loaded = load_edit_list(document)
        self.assertEqual(dumps_edit_list(loaded), original)

    def test_stable_ids_survive_many_round_trips(self) -> None:
        text = dumps_edit_list(make_timeline())
        expected_ids = [m["id"] for m in json.loads(text)["markers"]]

        for _ in range(250):
            timeline = load_edit_list(text)
            text = dumps_edit_list(timeline)

        self.assertEqual([m["id"] for m in json.loads(text)["markers"]], expected_ids)
        self.assertEqual(timeline.marker("media-1").event_type, "sync")

    def test_duplicate_ids_are_rejected(self) -> None:
        document = export_edit_list(make_timeline())
        document["markers"][1]["id"] = document["markers"][0]["id"]
        with self.assertRaises(MarkerError):
            load_edit_list(document)

    def test_float_ticks_are_rejected(self) -> None:
        document = export_edit_list(make_timeline())
        document["clips"][0]["source_in"] = 100_000_000.0
        with self.assertRaises(MarkerError):
            load_edit_list(document)

    def test_file_helpers(self) -> None:
        timeline = make_timeline()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "edit-list.json"
            write_edit_list(timeline, path)
            self.assertEqual(dumps_edit_list(read_edit_list(path)), dumps_edit_list(timeline))


class QuantizationTests(unittest.TestCase):
    def test_retime_uses_anchor_each_time_and_does_not_accumulate_error(self) -> None:
        timeline = Timeline()
        timeline.add_clip(
            "asset",
            timeline_start=7,
            source_in=100 * SECOND,
            source_out=110 * SECOND,
            speed=7,
            clip_id="clip",
        )
        timeline.add_media_marker(
            "clip",
            100 * SECOND + 1,
            marker_id="odd-media",
        )
        baseline = dumps_edit_list(timeline)
        baseline_timeline_tick = timeline.marker_timeline_tick("odd-media")

        for _ in range(1_000):
            timeline.set_speed("clip", Fraction(3, 7))
            timeline.move_clip("clip", 11)
            timeline.trim_clip(
                "clip", source_in=100 * SECOND + 123, source_out=109 * SECOND
            )
            timeline.trim_clip(
                "clip", source_in=100 * SECOND, source_out=110 * SECOND
            )
            timeline.move_clip("clip", 7)
            timeline.set_speed("clip", 7)

        self.assertEqual(timeline.marker("odd-media").anchor_tick, 100 * SECOND + 1)
        self.assertEqual(timeline.marker_timeline_tick("odd-media"), baseline_timeline_tick)
        self.assertEqual(dumps_edit_list(timeline), baseline)

    def test_round_trip_does_not_quantize_anchor_repeatedly(self) -> None:
        timeline = Timeline()
        timeline.add_clip(
            "asset",
            timeline_start=0,
            source_in=10,
            source_out=10 + 20_000,
            speed=Fraction(7, 3),
            clip_id="clip",
        )
        timeline.add_media_marker("clip", 10_001, marker_id="stable-id")
        expected_anchor = 10_001
        expected_media_tick = timeline.marker_media_tick("stable-id")
        text = dumps_edit_list(timeline)

        for _ in range(500):
            timeline = load_edit_list(text)
            self.assertEqual(timeline.marker("stable-id").anchor_tick, expected_anchor)
            self.assertEqual(timeline.marker_media_tick("stable-id"), expected_media_tick)
            text = dumps_edit_list(timeline)

        self.assertEqual(timeline.marker("stable-id").id, "stable-id")


if __name__ == "__main__":
    unittest.main()
