from __future__ import annotations

import unittest

from audio_merge import merge_projects
from audio_merge.normalize import canonical_json


BASE = {
    "version": 1,
    "name": "Song",
    "tracks": [
        {
            "id": "t1",
            "name": "Vocal",
            "params": {"gain": 0.0, "pan": 0.0,
                       "fx": {"reverb": 0.2, "delay": 0.0}},
            "clips": [
                {"id": "c1", "name": "verse",
                 "params": {"start": 0.0, "length": 16.0}},
                {"id": "c2", "name": "hook", "params": {"start": 32.0}},
            ],
        },
        {"id": "t2", "name": "Drums", "params": {"mute": False}, "clips": []},
    ],
}


def clone(doc):
    import json
    return json.loads(json.dumps(doc))


def find(tracks, track_id):
    return next(t for t in tracks if t["id"] == track_id)


class CleanMergeTests(unittest.TestCase):
    def test_independent_param_edits_auto_merge(self):
        left, right = clone(BASE), clone(BASE)
        find(left["tracks"], "t1")["params"]["pan"] = -0.3
        find(right["tracks"], "t1")["params"]["gain"] = 4.5

        result = merge_projects(BASE, left, right)
        self.assertEqual(result.conflicts, [])
        params = find(result.tree["tracks"], "t1")["params"]
        self.assertEqual(params["pan"], -0.3)
        self.assertEqual(params["gain"], 4.5)

    def test_add_and_delete_different_entities_auto_merge(self):
        left, right = clone(BASE), clone(BASE)
        left["tracks"].append({"id": "t3", "name": "Bass",
                               "params": {}, "clips": []})
        right["tracks"] = [t for t in right["tracks"] if t["id"] != "t2"]

        result = merge_projects(BASE, left, right)
        self.assertEqual(result.conflicts, [])
        ids = [t["id"] for t in result.tree["tracks"]]
        self.assertEqual(ids, ["t1", "t3"])

    def test_identical_concurrent_edits_auto_merge(self):
        left, right = clone(BASE), clone(BASE)
        find(left["tracks"], "t1")["name"] = "Lead"
        find(right["tracks"], "t1")["name"] = "Lead"
        result = merge_projects(BASE, left, right)
        self.assertEqual(result.conflicts, [])
        self.assertEqual(find(result.tree["tracks"], "t1")["name"], "Lead")

    def test_nested_param_and_key_removal(self):
        left, right = clone(BASE), clone(BASE)
        find(left["tracks"], "t1")["params"]["fx"]["reverb"] = 0.5
        fx = find(right["tracks"], "t1")["params"]["fx"]
        del fx["delay"]
        result = merge_projects(BASE, left, right)
        self.assertEqual(result.conflicts, [])
        merged_fx = find(result.tree["tracks"], "t1")["params"]["fx"]
        self.assertEqual(merged_fx, {"reverb": 0.5})


class SwapSymmetryTests(unittest.TestCase):
    def _clean_pair(self):
        left, right = clone(BASE), clone(BASE)
        find(left["tracks"], "t1")["params"]["pan"] = -0.3
        find(left["tracks"], "t1")["clips"][0]["params"]["length"] = 20.0
        left["tracks"].append({"id": "t3", "name": "Bass",
                               "params": {"gain": -1.0}, "clips": []})
        find(right["tracks"], "t1")["params"]["gain"] = 4.5
        right["name"] = "Song (mix B)"
        find(right["tracks"], "t2")["params"]["mute"] = True
        return left, right

    def test_clean_swap_produces_identical_output(self):
        left, right = self._clean_pair()
        ab = merge_projects(BASE, left, right)
        ba = merge_projects(BASE, right, left)
        self.assertEqual(ab.conflicts, [])
        self.assertEqual(ba.conflicts, [])
        self.assertEqual(
            canonical_json(ab.tree),
            canonical_json(ba.tree),
        )

    def test_conflict_swap_keeps_conflict_paths_and_values(self):
        left, right = clone(BASE), clone(BASE)
        find(left["tracks"], "t1")["params"]["gain"] = 2.0
        find(right["tracks"], "t1")["params"]["gain"] = -2.0
        # delete/modify on a clip
        find(left["tracks"], "t1")["clips"] = [
            c for c in find(left["tracks"], "t1")["clips"] if c["id"] != "c2"
        ]
        find(right["tracks"], "t1")["clips"][1]["name"] = "hook v2"

        ab = merge_projects(BASE, left, right)
        ba = merge_projects(BASE, right, left)
        ab_sig = sorted((c.kind, tuple(c.path)) for c in ab.conflicts)
        ba_sig = sorted((c.kind, tuple(c.path)) for c in ba.conflicts)
        self.assertEqual(ab_sig, ba_sig)
        # mirrored resolutions still yield the same final project
        for conflict in ab.conflicts:
            mirrored = ba.get(conflict.id)
            self.assertEqual(mirrored.path, conflict.path)
            if conflict.kind == "concurrent_edit":
                self.assertEqual(ab.get(conflict.id).left, mirrored.right)
        ab_res = {c.id: "left" for c in ab.conflicts}
        ba_res = {c.id: "right" for c in ba.conflicts}
        out_ab = merge_projects(BASE, left, right, ab_res).tree
        out_ba = merge_projects(BASE, right, left, ba_res).tree
        self.assertEqual(canonical_json(out_ab), canonical_json(out_ba))
