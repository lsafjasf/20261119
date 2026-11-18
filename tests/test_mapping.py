from fractions import Fraction
import unittest

from nested_timeline import Project, TimelineError, flatten, samples
from nested_timeline.models import Clip, NestedRef, Sequence


class TestMapping(unittest.TestCase):
    def test_speed_mapping_both_directions(self):
        ref = NestedRef("r", "child", start=4, duration=5, source_in=0, rate=2)
        self.assertEqual(ref.child_to_parent(0), 4)
        self.assertEqual(ref.child_to_parent(1), 6)
        self.assertEqual(ref.parent_to_child(6), 1)
        self.assertEqual(ref.parent_to_child(9), Fraction(5, 2))
        self.assertEqual(ref.source_out, Fraction(5, 2))

    def test_negative_offset_mapping(self):
        ref = NestedRef("r", "child", start=2, duration=10, source_in=-2, rate=1)
        self.assertEqual(ref.child_to_parent(-2), 2)
        self.assertEqual(ref.child_to_parent(0), 4)
        self.assertEqual(ref.parent_to_child(4), 0)

    def test_two_level_composition(self):
        project = samples.build_two_level_sample()
        events = {(e.kind, e.root_time) for e in project.render("main_nested")}
        self.assertIn(("video", 10), events)
        self.assertIn(("audio", 12), events)
        self.assertNotIn(("video", 14), events)
        # 外层只取 childC [1,3)，对应 grandG [0,1)；grand 的 4s 事件被窗口裁掉
        self.assertNotIn(("video", 18), events)
        self.assertEqual(len(events), 2)

    def test_window_clips_events_outside_ref(self):
        project = samples.build_speed_sample()
        times = [e.root_time for e in project.render("main_speed")]
        # 引用窗口只覆盖 childA [0, 2.5)，childA 的 2s 事件保留，无越界事件
        self.assertEqual(max(times), 8)
        self.assertEqual(min(times), 4)

    def test_negative_offset_visible_events_and_handle(self):
        project = samples.build_negative_offset_sample()
        events = project.render("main_negative")
        # 前置手柄 parent [2,4) 无内容；首个事件在 parent=4
        self.assertEqual(min(e.root_time for e in events), 4)
        by_kind = {(e.kind, e.root_time) for e in events}
        self.assertIn(("audio", Fraction(11, 2)), by_kind)
        self.assertIn(("video", 10), by_kind)


class TestCycle(unittest.TestCase):
    def test_cycle_detected_and_reported(self):
        project = samples.build_cycle_sample()
        self.assertEqual(project.detect_cycle("cycX"), ["cycX", "cycY", "cycX"])
        with self.assertRaises(TimelineError):
            project.render("cycX")
        with self.assertRaises(TimelineError):
            flatten(project, "cycX")

    def test_self_reference_is_cycle(self):
        seq = Sequence("self", refs=[NestedRef("back", "self", start=0, duration=2)])
        project = Project({"self": seq})
        with self.assertRaises(TimelineError):
            project.render("self")

    def test_missing_sequence_raises(self):
        seq = Sequence("root", refs=[NestedRef("ghost", "missing", start=0, duration=2)])
        project = Project({"root": seq})
        with self.assertRaises(TimelineError):
            project.render("root")

    def test_invalid_rate_rejected(self):
        with self.assertRaises(ValueError):
            NestedRef("r", "c", 0, 2, rate=0)
        with self.assertRaises(ValueError):
            Clip("c", 0, 2, rate=Fraction(-1))


if __name__ == "__main__":
    unittest.main()
