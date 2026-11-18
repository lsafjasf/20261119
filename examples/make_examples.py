"""Generate demo audio projects with both clean and conflicting edits."""

from __future__ import annotations

import json
from pathlib import Path


BASE = {
    "version": 1,
    "name": "Midnight Drive",
    "tracks": [
        {
            "id": "trk_vox",
            "name": "Lead Vocal",
            "params": {
                "gain_db": 0.0,
                "pan": 0.0,
                "effects": {"compressor": {"threshold_db": -18.0, "ratio": 3.0}},
            },
            "clips": [
                {"id": "clip_verse", "name": "Verse Take",
                 "params": {"start_sec": 0.0, "length_sec": 16.0,
                            "fade_in_sec": 0.0}},
                {"id": "clip_choir", "name": "Choir Pad",
                 "params": {"start_sec": 32.0, "length_sec": 8.0}},
            ],
        },
        {
            "id": "trk_drum",
            "name": "Drum Bus",
            "params": {"gain_db": -2.0, "mute": False},
            "clips": [
                {"id": "clip_kick", "name": "Kick Loop",
                 "params": {"start_sec": 0.0, "length_sec": 48.0}},
            ],
        },
    ],
}


def build_clean_pair():
    # Left branch: touches vocal pan and verse fade; adds a guitar track.
    left = json.loads(json.dumps(BASE))
    vox = left["tracks"][0]
    vox["params"]["pan"] = -0.25
    vox["clips"][0]["params"]["fade_in_sec"] = 0.4
    left["tracks"].append({
        "id": "trk_guitar",
        "name": "Rhythm Guitar",
        "params": {"gain_db": -5.0},
        "clips": [
            {"id": "clip_riff", "name": "Main Riff",
             "params": {"start_sec": 8.0, "length_sec": 24.0}},
        ],
    })

    # Right branch: independent edits on other fields and the drum bus.
    right = json.loads(json.dumps(BASE))
    rvox = right["tracks"][0]
    rvox["params"]["effects"]["compressor"]["threshold_db"] = -22.0
    rvox["clips"][1]["name"] = "Choir Pad (extended)"
    rvox["clips"][1]["params"]["length_sec"] = 12.0
    drum = next(t for t in right["tracks"] if t["id"] == "trk_drum")
    drum["params"]["gain_db"] = -3.5
    return left, right


def build_conflict_pair():
    left, right = build_clean_pair()

    # Both edit the same gain to different values (param conflict).
    next(t for t in left["tracks"] if t["id"] == "trk_drum")["params"]["gain_db"] = 0.5
    next(t for t in right["tracks"] if t["id"] == "trk_drum")["params"]["gain_db"] = -6.0

    # Left deletes the choir clip while right renames it (delete/modify).
    lvox = next(t for t in left["tracks"] if t["id"] == "trk_vox")
    lvox["clips"] = [c for c in lvox["clips"] if c["id"] != "clip_choir"]
    rvox = next(t for t in right["tracks"] if t["id"] == "trk_vox")
    next(c for c in rvox["clips"] if c["id"] == "clip_choir")["name"] = "Choir Finale"

    # Both add a track with the same stable id but different content.
    left["tracks"].append({
        "id": "trk_bass",
        "name": "Synth Bass",
        "params": {"gain_db": -4.0},
        "clips": [{"id": "clip_bass_a", "name": "Bass A",
                   "params": {"start_sec": 0.0, "length_sec": 32.0}}],
    })
    right["tracks"].append({
        "id": "trk_bass",
        "name": "Sub Bass",
        "params": {"gain_db": -8.0},
        "clips": [{"id": "clip_bass_a", "name": "Bass A",
                   "params": {"start_sec": 0.0, "length_sec": 32.0}}],
    })
    return left, right


def main() -> None:
    out = Path(__file__).resolve().parent
    clean_left, clean_right = build_clean_pair()
    conflict_left, conflict_right = build_conflict_pair()
    docs = {
        "base.json": BASE,
        "clean_left.json": clean_left,
        "clean_right.json": clean_right,
        "conflict_left.json": conflict_left,
        "conflict_right.json": conflict_right,
    }
    for name, doc in docs.items():
        (out / name).write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    print(f"wrote {len(docs)} example projects to {out}")


if __name__ == "__main__":
    main()
