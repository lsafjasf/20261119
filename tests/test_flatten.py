import unittest

from nested_timeline import Project, export_timeline, flatten, samples
from nested_timeline.models import Clip, NestedRef, Sequence


def keys(events, flattened):
    result = set()
    for event in events:
        name = event.source_clip if flattened else "/".join(event.path + (event.source_clip,))
        result.add((name, event.kind, event.root_time, event.source_time))
    return result


class TestFlattenConsistency(unittest.TestCase):
    def assert_nested_equals_flat(self, project, root_name):
        nested = project.render(root_name)
        flat_seq = flatten(project, root_name)
        flat_project = Project({flat_seq.name: flat_seq})
        flat_events = flat_project.render(flat_seq.name)
        self.assertEqual(keys(nested, False), keys(flat_events, True))

    def test_all_three_samples(self):
        self.assert_nested_equals_flat(samples.build_speed_sample(), "main_speed")
        self.assert_nested_equals_flat(
            samples.build_negative_offset_sample(), "main_negative"
        )
        self.assert_nested_equals_flat(
            samples.build_two_level_sample(), "main_nested"
        )

    def test_export_rejects_cycle_and_checks_consistency(self):
        with self.assertRaises(Exception):
            export_timeline(samples.build_cycle_sample(), "cycX")
        result = export_timeline(samples.build_speed_sample(), "main_speed")
        self.assertTrue(result["consistency_checked"])
        self.assertEqual(result["event_count"], 5)

    def test_audio_and_video_positions_match(self):
        project = samples.build_speed_sample()
        nested = {(e.kind, e.root_time) for e in project.render("main_speed")}
        flat_seq = flatten(project, "main_speed")
        flat = {
            (e.kind, e.root_time)
            for e in Project({flat_seq.name: flat_seq}).render(flat_seq.name)
        }
        self.assertEqual(nested, flat)
        self.assertTrue(any(kind == "audio" for kind, _ in flat))
        self.assertTrue(any(kind == "video" for kind, _ in flat))

    def test_flattened_clips_are_independent_copies(self):
        project = samples.build_speed_sample()
        flat_seq = flatten(project, "main_speed")
        flat_seq.clips[0].start += 100
        flat_seq.clips[0].video_events.append(99)
        child_clip = project.get("childA").clips[0]
        self.assertEqual(child_clip.start, 0)
        self.assertNotIn(99, child_clip.video_events)

    def test_parent_edit_propagates_but_keeps_child_intact(self):
        project = samples.build_speed_sample()
        ref = project.get("main_speed").find_ref("R1")
        before = sorted(e.root_time for e in project.render("main_speed"))
        ref.start = 8
        after = sorted(e.root_time for e in project.render("main_speed"))
        self.assertEqual([a - b for a, b in zip(after, before)], [4, 4, 4, 4, 4])
        self.assertEqual(project.get("childA").clips[0].start, 0)

    def test_partial_window_produces_trimmed_clip(self):
        child = Sequence(
            "c",
            [Clip("leaf", 0, 10, video_events=[0, 5, 10], audio_events=[5])],
        )
        main = Sequence(
            "m",
            refs=[NestedRef("ref", "c", start=0, duration=2, source_in=4, rate=1)],
        )
        project = Project({"c": child, "m": main})
        flat_seq = flatten(project, "m")
        self.assertEqual(len(flat_seq.clips), 1)
        clip = flat_seq.clips[0]
        self.assertEqual(clip.start, 0)
        self.assertEqual(clip.duration, 2)
        self.assertEqual(clip.source_in, 4)
        result = export_timeline(project, "m")
        self.assertEqual(result["event_count"], 2)  # video@5 与 audio@5


if __name__ == "__main__":
    unittest.main()
