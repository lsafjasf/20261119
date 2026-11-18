from __future__ import annotations

import json
import unittest

from audio_merge import Session, UnresolvedConflictsError
from audio_merge.session import SessionError
from audio_merge.normalize import (
    ProjectFormatError,
    canonical_json,
    normalize_project,
)

from tests.test_engine import BASE, clone, find


class ConflictDetectionTests(unittest.TestCase):
    def test_same_param_different_values_conflicts(self):
        left, right = clone(BASE), clone(BASE)
        find(left["tracks"], "t1")["params"]["gain"] = 2.0
        find(right["tracks"], "t1")["params"]["gain"] = -2.0
        session = Session(BASE, left, right)
        self.assertEqual(len(session.conflicts), 1)
        conflict = session.conflicts[0]
        self.assertEqual(conflict.kind, "concurrent_edit")
        self.assertEqual(conflict.path, ["t1", "params", "gain"])

    def test_delete_vs_modify_clip_conflicts(self):
        left, right = clone(BASE), clone(BASE)
        find(left["tracks"], "t1")["clips"] = [
            c for c in find(left["tracks"], "t1")["clips"] if c["id"] != "c2"
        ]
        find(right["tracks"], "t1")["clips"][1]["name"] = "hook renamed"
        session = Session(BASE, left, right)
        conflict = next(c for c in session.conflicts if c.kind == "delete_modify")
        self.assertEqual(conflict.entity_type, "clip")
        self.assertEqual(conflict.path, ["t1", "clips", "c2"])

    def test_delete_vs_unmodified_is_auto_merge(self):
        left, right = clone(BASE), clone(BASE)
        find(left["tracks"], "t1")["clips"] = [
            c for c in find(left["tracks"], "t1")["clips"] if c["id"] != "c2"
        ]
        session = Session(BASE, left, right)
        self.assertFalse(
            any(c.kind == "delete_modify" for c in session.conflicts)
        )
        clips = find(session.result.tree["tracks"], "t1")["clips"]
        self.assertEqual([c["id"] for c in clips], ["c1"])

    def test_param_delete_vs_modify_conflicts(self):
        left, right = clone(BASE), clone(BASE)
        del find(left["tracks"], "t1")["params"]["pan"]
        find(right["tracks"], "t1")["params"]["pan"] = 0.8
        session = Session(BASE, left, right)
        conflict = next(c for c in session.conflicts
                        if c.path == ["t1", "params", "pan"])
        self.assertEqual(conflict.kind, "delete_modify")

    def test_concurrent_add_same_id_conflicts(self):
        left, right = clone(BASE), clone(BASE)
        left["tracks"].append({"id": "t9", "name": "Bass A",
                               "params": {"gain": -3.0}, "clips": []})
        right["tracks"].append({"id": "t9", "name": "Bass B",
                                "params": {"gain": -6.0}, "clips": []})
        session = Session(BASE, left, right)
        conflict = next(c for c in session.conflicts if c.path == ["t9"])
        self.assertEqual(conflict.kind, "concurrent_add")


class ResolutionAndExportTests(unittest.TestCase):
    def _conflict_session(self):
        left, right = clone(BASE), clone(BASE)
        find(left["tracks"], "t1")["params"]["gain"] = 2.0
        find(right["tracks"], "t1")["params"]["gain"] = -2.0
        find(left["tracks"], "t1")["clips"] = [
            c for c in find(left["tracks"], "t1")["clips"] if c["id"] != "c2"
        ]
        find(right["tracks"], "t1")["clips"][1]["name"] = "hook v2"
        return Session(BASE, left, right)

    def test_export_blocked_while_unresolved(self):
        session = self._conflict_session()
        self.assertEqual(len(session.conflicts), 2)
        with self.assertRaises(UnresolvedConflictsError):
            session.export()

    def test_resolve_and_export_playable(self):
        session = self._conflict_session()
        clip_conflict = next(
            c for c in session.conflicts if c.kind == "delete_modify"
        )
        gain_conflict = next(
            c for c in session.conflicts if c.kind == "concurrent_edit"
        )
        session.resolve(gain_conflict.id, "right")
        session.resolve(clip_conflict.id, "delete")
        project = session.export()
        # normalize_project guarantees no internal conflict markers
        self.assertEqual(project["tracks"][0]["params"]["gain"], -2.0)
        clips = project["tracks"][0]["clips"]
        self.assertEqual([c["id"] for c in clips], ["c1"])
        # exported JSON contains no marker keys
        text = canonical_json(project)
        self.assertNotIn("__unresolved__", text)
        self.assertNotIn("__conflict__", text)

    def test_invalid_choice_rejected(self):
        session = self._conflict_session()
        edit_conflict = next(
            c for c in session.conflicts if c.kind == "concurrent_edit"
        )
        with self.assertRaises(SessionError):
            session.resolve(edit_conflict.id, "delete")

    def test_unknown_conflict_rejected(self):
        session = self._conflict_session()
        with self.assertRaises(KeyError):
            session.resolve("C99", "left")

    def test_resolution_is_recorded_in_log(self):
        session = self._conflict_session()
        first = session.conflicts[0]
        session.resolve(first.id, "left")
        actions = [event["action"] for event in session.result.log]
        self.assertIn("resolve", actions)
        resolve_event = next(e for e in session.result.log
                             if e["action"] == "resolve")
        self.assertEqual(resolve_event["choice"], "left")

    def test_reset_reopens_conflict(self):
        session = self._conflict_session()
        session.resolve(session.conflicts[0].id, "left")
        self.assertEqual(len(session.result.unresolved), 1)
        session.reset(session.conflicts[0].id)
        self.assertEqual(len(session.result.unresolved), 2)


class NormalizationTests(unittest.TestCase):
    def test_normalization_is_deterministic_and_sorted(self):
        doc = {
            "name": "X",
            "tracks": [
                {"id": "z", "name": "Z", "params": {"b": 1, "a": 2},
                 "clips": [{"id": "c2", "name": "two", "params": {}},
                           {"id": "c1", "name": "one", "params": {}}]},
                {"id": "a", "name": "A", "params": {}, "clips": []},
            ],
        }
        once = canonical_json(normalize_project(doc))
        again = canonical_json(normalize_project(json.loads(once)))
        self.assertEqual(once, again)
        parsed = json.loads(once)
        self.assertEqual([t["id"] for t in parsed["tracks"]], ["a", "z"])
        self.assertEqual(
            [c["id"] for c in parsed["tracks"][1]["clips"]], ["c1", "c2"]
        )

    def test_validation_rejects_bad_inputs(self):
        with self.assertRaises(ProjectFormatError):
            normalize_project({"tracks": [{"name": "no id"}]})
        with self.assertRaises(ProjectFormatError):
            normalize_project({"tracks": [
                {"id": "t1", "clips": "nope"},
            ]})
        with self.assertRaises(ProjectFormatError):
            normalize_project({"tracks": [
                {"id": "t1", "params": {"k": object()}},
            ]})


if __name__ == "__main__":
    unittest.main()
