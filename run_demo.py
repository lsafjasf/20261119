"""End-to-end demo: clean merge, conflict resolution, swap-symmetry check.

Run with:  python3 run_demo.py
"""

from __future__ import annotations

import json
from pathlib import Path

from audio_merge import Session, UnresolvedConflictsError
from audio_merge.normalize import canonical_json, load_project


ROOT = Path(__file__).resolve().parent
EXAMPLES = ROOT / "examples"
ARTIFACTS = ROOT / "artifacts"


def main() -> int:
    base = load_project(EXAMPLES / "base.json")
    clean_left = load_project(EXAMPLES / "clean_left.json")
    clean_right = load_project(EXAMPLES / "clean_right.json")
    conflict_left = load_project(EXAMPLES / "conflict_left.json")
    conflict_right = load_project(EXAMPLES / "conflict_right.json")

    print("== 1. Clean merge (independent edits) ==")
    clean = Session(base, clean_left, clean_right)
    print(f"conflicts: {len(clean.conflicts)} (expected 0)")
    clean_project = clean.export()

    print("\n== 2. Swap left/right must produce identical output ==")
    swapped = Session(base, clean_right, clean_left)
    same = canonical_json(swapped.export()) == canonical_json(clean_project)
    print(f"canonical outputs identical after swap: {same}")

    print("\n== 3. Conflicting merge ==")
    session = Session(base, conflict_left, conflict_right)
    for conflict in session.conflicts:
        print(f"  {conflict.id} [{conflict.kind}] {conflict.label}")
    try:
        session.export()
    except UnresolvedConflictsError as exc:
        print(f"export blocked as expected: {exc}")

    print("\n== 4. Resolve item by item, then export ==")
    choices = {"C1": "left", "C2": "right", "C3": "delete"}
    for conflict_id, choice in choices.items():
        session.resolve(conflict_id, choice)
    merged = session.export()

    ARTIFACTS.mkdir(exist_ok=True)
    (ARTIFACTS / "merged_project.json").write_text(
        canonical_json(merged), encoding="utf-8"
    )
    (ARTIFACTS / "merge_log.json").write_text(
        canonical_json(session.result.log), encoding="utf-8"
    )
    print(f"wrote {ARTIFACTS / 'merged_project.json'}")
    print(f"wrote {ARTIFACTS / 'merge_log.json'}")
    print(f"track count: {len(merged['tracks'])}")

    print("\n== 5. Conflict swap symmetry ==")
    mirrored = Session(base, conflict_right, conflict_left)
    paths_a = sorted((c.kind, tuple(c.path)) for c in session.conflicts)
    paths_b = sorted((c.kind, tuple(c.path)) for c in mirrored.conflicts)
    print(f"conflict paths invariant under swap: {paths_a == paths_b}")

    ok = same and paths_a == paths_b
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
