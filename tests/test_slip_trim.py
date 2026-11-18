import unittest

from timeline import (
    Clip,
    History,
    Keyframe,
    Marker,
    MediaSource,
    Project,
    SlipTrimCommand,
    clamp_slip_delta,
)


def make_clip(
    source_length: float = 20.0,
    target_start: float = 5.0,
    source_in: float = 4.0,
    source_out: float = 10.0,
    markers=(),
    keyframes=(),
) -> Clip:
    source = MediaSource(name="A.mp4", length=source_length)
    return Clip(
        clip_id="c1",
        source=source,
        target_start=target_start,
        source_in=source_in,
        source_out=source_out,
        markers=list(markers),
        keyframes=list(keyframes),
    )


class BasicSlipTests(unittest.TestCase):
    def test_in_and_out_move_together_and_duration_is_fixed(self):
        clip = make_clip()
        before = (clip.source_in, clip.source_out, clip.duration,
                  clip.target_start, clip.target_end)

        result = History().execute(SlipTrimCommand(clip, 2.5))

        self.assertAlmostEqual(clip.source_in, 6.5)
        self.assertAlmostEqual(clip.source_out, 12.5)
        self.assertAlmostEqual(clip.duration, before[2])
        self.assertEqual((clip.target_start, clip.target_end),
                         (before[3], before[4]))
        self.assertAlmostEqual(result.applied_delta, 2.5)
        self.assertFalse(result.blocked)

    def test_negative_delta_slides_toward_head(self):
        clip = make_clip()
        SlipTrimCommand(clip, -3.0).do()
        self.assertAlmostEqual(clip.source_in, 1.0)
        self.assertAlmostEqual(clip.source_out, 7.0)
        self.assertAlmostEqual(clip.duration, 6.0)

    def test_drag_in_point_derives_delta_and_out_follows(self):
        clip = make_clip()
        command = SlipTrimCommand.drag_in_point(clip, 8.0)
        command.do()
        self.assertAlmostEqual(clip.source_in, 8.0)
        self.assertAlmostEqual(clip.source_out, 14.0)

    def test_zero_delta_is_a_noop_but_still_undoable(self):
        history = History()
        clip = make_clip()
        history.execute(SlipTrimCommand(clip, 0.0))
        history.undo()
        self.assertAlmostEqual(clip.source_in, 4.0)
        self.assertAlmostEqual(clip.source_out, 10.0)


class BoundaryTests(unittest.TestCase):
    def test_slip_past_head_is_blocked_at_zero(self):
        clip = make_clip(source_in=4.0, source_out=10.0)
        result = SlipTrimCommand(clip, -9.0).do()
        self.assertAlmostEqual(clip.source_in, 0.0)
        self.assertAlmostEqual(clip.source_out, 6.0)
        self.assertTrue(result.blocked)
        self.assertAlmostEqual(result.applied_delta, -4.0)

    def test_slip_past_tail_is_blocked_at_source_length(self):
        clip = make_clip(source_length=20.0, source_in=4.0, source_out=10.0)
        result = SlipTrimCommand(clip, 50.0).do()
        self.assertAlmostEqual(clip.source_in, 14.0)
        self.assertAlmostEqual(clip.source_out, 20.0)
        self.assertTrue(result.blocked)
        self.assertAlmostEqual(result.applied_delta, 10.0)

    def test_in_point_at_source_head_cannot_slip_earlier(self):
        clip = make_clip(source_in=0.0, source_out=6.0)
        applied, blocked = clamp_slip_delta(clip, -1.0)
        self.assertEqual(applied, 0.0)
        self.assertTrue(blocked)
        result = SlipTrimCommand(clip, -1.0).do()
        self.assertAlmostEqual(clip.source_in, 0.0)
        self.assertAlmostEqual(clip.source_out, 6.0)

    def test_out_point_at_source_tail_cannot_slip_later(self):
        clip = make_clip(source_length=20.0, source_in=14.0, source_out=20.0)
        result = SlipTrimCommand(clip, 1.0).do()
        self.assertAlmostEqual(clip.source_in, 14.0)
        self.assertAlmostEqual(clip.source_out, 20.0)
        self.assertTrue(result.blocked)

    def test_sliding_exactly_to_boundary_is_allowed_not_blocked(self):
        clip = make_clip(source_in=4.0, source_out=10.0)
        head = SlipTrimCommand(clip, -4.0).do()
        self.assertFalse(head.blocked)
        self.assertAlmostEqual(clip.source_in, 0.0)
        other = make_clip(source_in=4.0, source_out=10.0)
        tail = SlipTrimCommand(other, 10.0).do()
        self.assertFalse(tail.blocked)
        self.assertAlmostEqual(other.source_out, 20.0)

    def test_blocked_slip_never_overshoots_with_float_inputs(self):
        clip = make_clip(source_length=3.333, source_in=1.111,
                         source_out=2.222)
        SlipTrimCommand(clip, 99.0).do()
        self.assertLessEqual(clip.source_out, clip.source.length + 1e-9)
        self.assertAlmostEqual(clip.duration, 2.222 - 1.111)


class MarkerMappingTests(unittest.TestCase):
    def setUp(self):
        self.markers = (
            Marker(5.0, "hit"),
            Marker(8.0, "beat"),
            Marker(12.0, "outside"),
            Marker(16.0, "tail"),
        )
        self.keyframes = (Keyframe(6.0, 0.25), Keyframe(9.0, 0.75))
        self.clip = make_clip(
            source_in=4.0,
            source_out=10.0,
            target_start=5.0,
            markers=self.markers,
            keyframes=self.keyframes,
        )

    def test_display_time_moves_with_the_window(self):
        SlipTrimCommand(self.clip, 2.0).do()
        # source anchor times are unchanged
        self.assertEqual([m.source_time for m in self.clip.markers],
                         [5.0, 8.0, 12.0, 16.0])
        visible = dict(
            (m.label, display)
            for m, display in self.clip.visible_markers()
        )
        # display = target_start + (source_time - source_in)
        self.assertAlmostEqual(visible["beat"], 5.0 + (8.0 - 6.0))
        # a marker outside the window still has a deterministic mapping
        self.assertAlmostEqual(
            self.clip.source_to_target(5.0), 5.0 + (5.0 - 6.0)
        )

    def test_markers_leaving_or_entering_the_window(self):
        SlipTrimCommand(self.clip, 2.0).do()  # window becomes 6..12
        labels = [m.label for m, _ in self.clip.visible_markers()]
        self.assertNotIn("hit", labels)       # source 5.0 now before in
        self.assertIn("beat", labels)         # source 8.0 still inside
        self.assertIn("outside", labels)      # source 12.0 now at out edge

    def test_keyframes_remap_the_same_way(self):
        SlipTrimCommand(self.clip, -3.0).do()  # window becomes 1..7
        values = {k.value: display for k, display in
                  self.clip.visible_keyframes()}
        self.assertAlmostEqual(values[0.25], 5.0 + (6.0 - 1.0))
        self.assertNotIn(0.75, values)         # source 9.0 beyond out 7.0

    def test_clip_view_reports_both_intervals_and_mappings(self):
        project = Project("demo", [self.clip])
        SlipTrimCommand(self.clip, 1.5).do()
        view = project.clip_view("c1")
        self.assertEqual(view["source_interval"], (5.5, 11.5))
        self.assertEqual(view["target_interval"], (5.0, 11.0))
        marker = next(m for m in view["markers"] if m["label"] == "beat")
        self.assertAlmostEqual(marker["source_time"], 8.0)
        self.assertAlmostEqual(marker["display_time"],
                               5.0 + (8.0 - 5.5))

    def test_blocked_slip_still_remaps_at_the_clamped_position(self):
        result = SlipTrimCommand(self.clip, 99.0).do()
        self.assertTrue(result.blocked)
        visible = {m.label for m, _ in self.clip.visible_markers()}
        self.assertEqual(visible, {"tail"})  # window clamped to 14..20


class UndoRedoTests(unittest.TestCase):
    def setUp(self):
        self.history = History()
        self.clip = make_clip(
            source_in=4.0,
            source_out=10.0,
            target_start=5.0,
            markers=(Marker(5.0, "hit"), Marker(9.5, "edge")),
            keyframes=(Keyframe(7.0, 0.5),),
        )

    def test_undo_restores_both_endpoints_and_marker_mappings(self):
        original_view = Project("p", [self.clip]).clip_view("c1")
        self.history.execute(SlipTrimCommand(self.clip, 2.0))
        self.history.execute(SlipTrimCommand(self.clip, -3.5))

        self.history.undo()
        self.assertAlmostEqual(self.clip.source_in, 6.0)
        self.assertAlmostEqual(self.clip.source_out, 12.0)

        self.history.undo()
        self.assertAlmostEqual(self.clip.source_in, 4.0)
        self.assertAlmostEqual(self.clip.source_out, 10.0)
        restored = Project("p", [self.clip]).clip_view("c1")
        self.assertEqual(restored["source_interval"],
                         original_view["source_interval"])
        self.assertEqual(restored["target_interval"],
                         original_view["target_interval"])
        self.assertEqual(
            [(m["source_time"], m["display_time"])
             for m in restored["markers"]],
            [(m["source_time"], m["display_time"])
             for m in original_view["markers"]],
        )
        self.assertEqual(
            [(k["source_time"], k["display_time"])
             for k in restored["keyframes"]],
            [(k["source_time"], k["display_time"])
             for k in original_view["keyframes"]],
        )

    def test_redo_reapplies_the_same_clamped_window(self):
        result = self.history.execute(SlipTrimCommand(self.clip, 99.0))
        self.assertTrue(result.blocked)
        self.history.undo()
        self.assertAlmostEqual(self.clip.source_in, 4.0)
        self.history.redo()
        self.assertAlmostEqual(self.clip.source_in, 14.0)
        self.assertAlmostEqual(self.clip.source_out, 20.0)

    def test_new_command_clears_the_redo_stack(self):
        self.history.execute(SlipTrimCommand(self.clip, 1.0))
        self.history.undo()
        self.assertTrue(self.history.can_redo)
        self.history.execute(SlipTrimCommand(self.clip, 0.5))
        self.assertFalse(self.history.can_redo)
        self.history.undo()
        self.assertAlmostEqual(self.clip.source_in, 4.0)

    def test_undo_restores_visibility_of_markers_that_left_window(self):
        self.history.execute(SlipTrimCommand(self.clip, 2.0))  # 6..12
        self.assertNotIn(
            "hit",
            {m.label for m, _ in self.clip.visible_markers()},
        )
        self.history.undo()
        self.assertEqual(
            {"hit", "edge"},
            {m.label for m, _ in self.clip.visible_markers()},
        )

    def test_empty_history_raises(self):
        with self.assertRaises(RuntimeError):
            self.history.undo()


if __name__ == "__main__":
    unittest.main()
