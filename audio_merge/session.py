"""Interactive merge session: per-conflict resolution and guarded export."""

from __future__ import annotations

from typing import Any

from .engine import (
    MergeResult,
    merge_projects,
)
from .normalize import canonical_json, normalize_project


class SessionError(ValueError):
    """Invalid session operation (unknown conflict, illegal choice)."""


class UnresolvedConflictsError(SessionError):
    """Raised when an export is attempted with open conflicts."""


_VALUE_CHOICES = {"left", "right", "base"}


class Session:
    def __init__(self, base: dict, left: dict, right: dict) -> None:
        self.base = base
        self.left = left
        self.right = right
        self.resolutions: dict[str, str] = {}
        self.result: MergeResult = merge_projects(base, left, right)

    # -- conflict operations ------------------------------------------------

    @property
    def conflicts(self) -> list:
        return self.result.conflicts

    def resolve(self, conflict_id: str, choice: str) -> None:
        conflict = self.result.get(conflict_id)
        allowed = set(_VALUE_CHOICES)
        if conflict.kind == "delete_modify":
            allowed.add("delete")
        if choice not in allowed:
            raise SessionError(
                f"conflict {conflict_id}: choice {choice!r} not in "
                f"{sorted(allowed)}"
            )
        self.resolutions[conflict_id] = choice
        self._remerge()

    def resolve_all(self, choice: str) -> None:
        for conflict in self.result.conflicts:
            allowed = set(_VALUE_CHOICES)
            if conflict.kind == "delete_modify":
                allowed.add("delete")
            if choice in allowed:
                self.resolutions[conflict.id] = choice
        self._remerge()

    def reset(self, conflict_id: str | None = None) -> None:
        if conflict_id is None:
            self.resolutions.clear()
        else:
            self.result.get(conflict_id)
            self.resolutions.pop(conflict_id, None)
        self._remerge()

    def _remerge(self) -> None:
        self.result = merge_projects(
            self.base, self.left, self.right, dict(self.resolutions)
        )

    # -- snapshots and export ----------------------------------------------

    def snapshot(self) -> dict:
        result = self.result
        return {
            "schema": "audio-merge-session/1",
            "conflicts": [item.to_dict() for item in result.conflicts],
            "unresolvedCount": len(result.unresolved),
            "resolutions": dict(self.resolutions),
            "previewTree": result.tree,
            "log": result.log,
        }

    def export(self) -> dict:
        """Return the normalized, playable project or raise."""
        if self.result.unresolved:
            ids = ", ".join(item.id for item in self.result.unresolved)
            raise UnresolvedConflictsError(
                f"cannot export: unresolved conflicts remain ({ids})"
            )
        tree = self.result.tree
        _assert_playable(tree)
        project = normalize_project(_clean_tree(tree))
        self.result.record("export", status="ok")
        return project

    def export_json(self) -> str:
        return canonical_json(self.export())


def _assert_playable(node: Any, path: str = "$") -> None:
    if isinstance(node, dict):
        if "__unresolved__" in node:
            raise UnresolvedConflictsError(
                f"cannot export: unresolved marker at {path}"
            )
        if "__conflict__" in node:
            raise UnresolvedConflictsError(
                f"cannot export: unresolved entity marker at {path}"
            )
        for key, value in node.items():
            _assert_playable(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _assert_playable(value, f"{path}[{index}]")


def _clean_tree(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: _clean_tree(value)
            for key, value in node.items()
            if key not in {"__conflict__", "__unresolved__"}
        }
    if isinstance(node, list):
        return [_clean_tree(value) for value in node]
    return node
