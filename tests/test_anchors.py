from __future__ import annotations

import unittest
from fractions import Fraction

from timeline_markers import Anchor, MarkerError, Timeline

SECOND = 1_000_000


def make_timeline() -> Timeline:
    timeline = Timeline()
    timeline.add_clip(
        "asset-a",
        timeline_start=10 * SECOND,
        source_in=100 * SECOND,
        source_out=110 * SECOND,
        clip_id="clip-a",
    )
    timeline.add_media_marker(
        "clip-a",
        102 * SECOND,
        marker_id="media-marker",
        color="#ff0000",
        note="source sync",
        event_type="sync",
    )
    timeline.add_clip_anchor_marker(
        "clip-a",
        4 * SECOND,
        marker_id="clip-marker",
        color="blue",
        note="four seconds in",
        event_type="cue",
    )
    timeline.add_timeline_marker(
        20 * SECOND,
        marker_id="global-marker",
        color="green",
        note="chapter",
        event_type="chapter",
    )
    return timeline


def resolved(timeline: Timeline) -> dict[str, tuple[int | None, int | None, bool]]:
    return {
        occurrence.id: (
            occurrence.timeline_tick,
            occurrence.media_tick,
            occurrence.visible,
        )
        for occurrence in timeline.occurrences(include_hidden=True)
    }


class MarkerMetadataTests(unittest.TestCase):
    def test_marker_fields_and_stable_id_are_preserved_on_update(self) -> None:
        timeline = make_timeline()
        marker = timeline.marker("media-marker")
        updated = timeline.update_marker(
            marker.id,
            color="#123456",
            note="updated",
            event_type="action",
        )

        self.assertEqual(updated.id, marker.id)
        self.assertEqual(updated.anchor, Anchor.MEDIA)
        self.assertEqual(updated.anchor_tick, marker.anchor_tick)
        self.assertEqual(updated.color, "#123456")
        self.assertEqual(updated.note, updated.comment)
        self.assertEqual(updated.event_type, "action")

    def test_global_markers_are_timeline_owned(self) -> None:
        timeline = make_timeline()
        marker = timeline.marker("global-marker")
        self.assertEqual(marker.anchor, Anchor.GLOBAL)
        self.assertIsNone(timeline.marker_media_tick(marker.id))
        with self.assertRaises(MarkerError):
            timeline.add_clip_marker(
                "clip-a", Anchor.GLOBAL, 1, marker_id="invalid-global"
            )


class AnchorSemanticsTests(unittest.TestCase):
    def test_initial_positions(self) -> None:
        timeline = make_timeline()
        self.assertEqual(resolved(timeline)["media-marker"], (12 * SECOND, 102 * SECOND, True))
        self.assertEqual(resolved(timeline)["clip-marker"], (14 * SECOND, 104 * SECOND, True))
        self.assertEqual(resolved(timeline)["global-marker"], (20 * SECOND, None, True))

    def test_move_keeps_anchor_values_and_only_shifts_clip_occurrences(self) -> None:
        timeline = make_timeline()
        timeline.move_clip("clip-a", 15 * SECOND)

        values = resolved(timeline)
        self.assertEqual(values["media-marker"], (17 * SECOND, 102 * SECOND, True))
        self.assertEqual(values["clip-marker"], (19 * SECOND, 104 * SECOND, True))
        self.assertEqual(values["global-marker"], (20 * SECOND, None, True))
        self.assertEqual(timeline.marker("media-marker").anchor_tick, 102 * SECOND)
        self.assertEqual(timeline.marker("clip-marker").anchor_tick, 4 * SECOND)

    def test_trim_in_hides_media_anchor_but_not_clip_anchor(self) -> None:
        timeline = make_timeline()
        timeline.trim_clip("clip-a", source_in=103 * SECOND)

        values = resolved(timeline)
        self.assertEqual(values["media-marker"], (9 * SECOND, 102 * SECOND, False))
        self.assertEqual(values["clip-marker"], (14 * SECOND, 107 * SECOND, True))
        self.assertEqual(values["global-marker"], (20 * SECOND, None, True))

        timeline.trim_clip("clip-a", source_in=101 * SECOND)
        values = resolved(timeline)
        self.assertEqual(values["media-marker"], (11 * SECOND, 102 * SECOND, True))
        self.assertEqual(values["clip-marker"], (14 * SECOND, 105 * SECOND, True))

    def test_trim_out_hides_clip_anchor_without_deleting_it(self) -> None:
        timeline = make_timeline()
        timeline.trim_clip("clip-a", source_out=103 * SECOND)

        values = resolved(timeline)
        self.assertEqual(values["clip-marker"], (14 * SECOND, 104 * SECOND, False))
        self.assertEqual(values["media-marker"], (12 * SECOND, 102 * SECOND, True))
        self.assertIn("clip-marker", values)

    def test_speed_retimes_media_anchor_but_not_clip_or_global_anchor(self) -> None:
        timeline = make_timeline()
        timeline.set_speed("clip-a", 2)

        values = resolved(timeline)
        self.assertEqual(values["media-marker"], (11 * SECOND, 102 * SECOND, True))
        self.assertEqual(values["clip-marker"], (14 * SECOND, 108 * SECOND, True))
        self.assertEqual(values["global-marker"], (20 * SECOND, None, True))

        timeline.set_speed("clip-a", Fraction(1, 2))
        values = resolved(timeline)
        self.assertEqual(values["media-marker"], (14 * SECOND, 102 * SECOND, True))
        self.assertEqual(values["clip-marker"], (14 * SECOND, 102 * SECOND, True))

    def test_delete_clip_cascades_only_its_private_markers(self) -> None:
        timeline = make_timeline()
        timeline.add_clip(
            "asset-b",
            timeline_start=0,
            source_in=0,
            source_out=5 * SECOND,
            clip_id="clip-b",
        )
        timeline.add_media_marker("clip-b", SECOND, marker_id="other-media")

        removed = timeline.remove_clip("clip-a")
        self.assertEqual({marker.id for marker in removed}, {"media-marker", "clip-marker"})
        with self.assertRaises(MarkerError):
            timeline.marker("media-marker")
        with self.assertRaises(MarkerError):
            timeline.marker("clip-marker")
        self.assertEqual(timeline.marker("global-marker").id, "global-marker")
        self.assertEqual(timeline.marker("other-media").id, "other-media")

    def test_duplicate_marker_and_clip_ids_are_rejected(self) -> None:
        timeline = make_timeline()
        with self.assertRaises(MarkerError):
            timeline.add_timeline_marker(1, marker_id="media-marker")
        with self.assertRaises(MarkerError):
            timeline.add_clip(
                "asset",
                timeline_start=0,
                source_in=0,
                source_out=1,
                clip_id="clip-a",
            )


if __name__ == "__main__":
    unittest.main()
