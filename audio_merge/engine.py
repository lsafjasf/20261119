"""Three-way merge engine for simplified audio projects.

The engine is side-agnostic: swapping the left and right inputs must not
change the set of conflicts or the automatically merged values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .normalize import normalize_project


class _Marker:
    def __init__(self, label: str) -> None:
        self.label = label

    def __repr__(self) -> str:
        return f"<{self.label}>"


MISSING = _Marker("MISSING")
DELETED = _Marker("DELETED")


def unresolved_placeholder(conflict_id: str) -> dict:
    """Marker embedded in preview trees for unresolved conflict paths."""
    return {"__unresolved__": conflict_id}


@dataclass
class Conflict:
    id: str
    kind: str  # delete_modify | concurrent_edit | concurrent_add
    entity_type: str  # track | clip | param
    path: list[str]
    label: str
    base: Any
    left: Any
    right: Any
    resolution: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "entityType": self.entity_type,
            "path": list(self.path),
            "label": self.label,
            "base": _jsonable(self.base),
            "left": _jsonable(self.left),
            "right": _jsonable(self.right),
            "resolution": self.resolution,
        }


@dataclass
class MergeResult:
    base: dict
    left: dict
    right: dict
    conflicts: list[Conflict] = field(default_factory=list)
    log: list[dict] = field(default_factory=list)
    _counter: int = 0
    tree: dict | None = None

    def next_conflict_id(self) -> str:
        self._counter += 1
        return f"C{self._counter}"

    def record(self, action: str, **detail: Any) -> None:
        event = {"action": action}
        event.update(detail)
        self.log.append(event)

    def add_conflict(self, conflict: Conflict) -> None:
        self.conflicts.append(conflict)
        self.record(
            "conflict",
            conflictId=conflict.id,
            kind=conflict.kind,
            path=list(conflict.path),
            label=conflict.label,
        )

    @property
    def unresolved(self) -> list[Conflict]:
        return [item for item in self.conflicts if item.resolution is None]

    def get(self, conflict_id: str) -> Conflict:
        for conflict in self.conflicts:
            if conflict.id == conflict_id:
                return conflict
        raise KeyError(f"unknown conflict id: {conflict_id}")


def _jsonable(value: Any) -> Any:
    if value is MISSING:
        return {"__marker__": "missing"}
    if value is DELETED:
        return {"__marker__": "deleted"}
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def equal(left: Any, right: Any) -> bool:
    """Strict deep equality (booleans are not numbers)."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        if left.keys() != right.keys():
            return False
        return all(equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(
            equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def merge_value(
    result: MergeResult,
    path: list[str],
    label: str,
    base: Any,
    left: Any,
    right: Any,
    entity_type: str = "param",
) -> Any:
    """Three-way merge of scalar/list/dict values with conflict recording."""
    if equal(left, base) and equal(right, base):
        return _unwrap(base)

    # Explicit deletions take precedence over recursion.
    if left is DELETED or right is DELETED:
        return _merge_deletion(result, path, label, base, left, right, entity_type)

    # Recurse while every concrete side is a dict.
    if _is_dictish(base) and _is_dictish(left) and _is_dictish(right):
        return _merge_nested_dict(result, path, base, left, right, entity_type)

    if equal(left, base):
        result.record("auto_merge", path=list(path), source="right", label=label)
        return _unwrap(right)
    if equal(right, base):
        result.record("auto_merge", path=list(path), source="left", label=label)
        return _unwrap(left)
    if equal(left, right):
        result.record("auto_merge", path=list(path), source="both", label=label)
        return _unwrap(left)

    conflict = Conflict(
        id=result.next_conflict_id(),
        kind="concurrent_add" if base is MISSING else "concurrent_edit",
        entity_type=entity_type,
        path=list(path),
        label=label,
        base=base,
        left=left,
        right=right,
    )
    result.add_conflict(conflict)
    return unresolved_placeholder(conflict.id)


def _is_dictish(value: Any) -> bool:
    return value is MISSING or isinstance(value, dict)


def _unwrap(value: Any) -> Any:
    return {} if value is MISSING else value


def _merge_nested_dict(
    result: MergeResult,
    path: list[str],
    base: Any,
    left: Any,
    right: Any,
    entity_type: str,
) -> dict:
    base_map = {} if base is MISSING else base
    left_map = {} if left is MISSING else left
    right_map = {} if right is MISSING else right
    merged: dict = {}
    for key in sorted(set(base_map) | set(left_map) | set(right_map)):
        child_path = path + [key]
        base_present = key in base_map
        left_value = left_map[key] if key in left_map else (
            DELETED if base_present else MISSING)
        right_value = right_map[key] if key in right_map else (
            DELETED if base_present else MISSING)
        child = merge_value(
            result,
            child_path,
            ".".join(child_path),
            base_map.get(key, MISSING),
            left_value,
            right_value,
            entity_type,
        )
        if child is not DELETED and child is not MISSING:
            merged[key] = child
    return merged


def _merge_deletion(
    result: MergeResult,
    path: list[str],
    label: str,
    base: Any,
    left: Any,
    right: Any,
    entity_type: str,
) -> Any:
    left_deleted = left is DELETED
    right_deleted = right is DELETED
    if left_deleted and right_deleted:
        result.record("auto_merge", path=list(path), source="both",
                      label=label, detail="deleted")
        return DELETED

    deleting_side = "left" if left_deleted else "right"
    modified = right if left_deleted else left
    if equal(modified, base):
        result.record("auto_merge", path=list(path), source=deleting_side,
                      label=label, detail="delete_wins_unchanged")
        return DELETED

    conflict = Conflict(
        id=result.next_conflict_id(),
        kind="delete_modify",
        entity_type=entity_type,
        path=list(path),
        label=label,
        base=base,
        left=left,
        right=right,
    )
    result.add_conflict(conflict)
    return unresolved_placeholder(conflict.id)


# ---------------------------------------------------------------------------
# Entity-level merging (tracks and clips)
# ---------------------------------------------------------------------------


def _index(entities: list[dict]) -> dict[str, dict]:
    return {entity["id"]: entity for entity in entities}


def merge_projects(
    base_doc: dict,
    left_doc: dict,
    right_doc: dict,
    resolutions: dict[str, str] | None = None,
) -> MergeResult:
    """Merge three project documents.

    ``resolutions`` maps conflict id to one of: ``left``, ``right``,
    ``base``, ``delete`` (delete/modify only).
    """
    resolutions = resolutions or {}
    base = normalize_project(base_doc)
    left = normalize_project(left_doc)
    right = normalize_project(right_doc)
    discovery = MergeResult(base=base, left=left, right=right)
    discovery.record(
        "start",
        baseTracks=len(base["tracks"]),
        leftTracks=len(left["tracks"]),
        rightTracks=len(right["tracks"]),
    )

    # Phase 1: discover conflicts with path-ordered stable ids.
    preview_tracks = _merge_entity_collection(
        discovery, base["tracks"], left["tracks"], right["tracks"],
        entity_type="track", path=[],
    )
    preview = {
        "version": 1,
        "name": _merge_project_name(discovery, base, left, right),
        "tracks": preview_tracks,
    }
    _stabilize_conflict_ids(discovery, preview)

    # Phase 2: apply resolutions and rebuild the output tree.
    result = MergeResult(base=base, left=left, right=right)
    for event in discovery.log:
        result.log.append(dict(event))
    result._counter = len(discovery.conflicts)
    for conflict in discovery.conflicts:
        result.conflicts.append(conflict)
        choice = resolutions.get(conflict.id)
        if choice is not None:
            conflict.resolution = choice
            result.record("resolve", conflictId=conflict.id, choice=choice,
                          path=list(conflict.path))

    tree = _apply_resolutions(preview, result)
    result.record("complete",
                  conflicts=len(result.conflicts),
                  unresolved=len(result.unresolved))
    result.tree = tree
    return result


def _stabilize_conflict_ids(result: MergeResult, tree: dict) -> None:
    """Order conflicts by path and retag every placeholder in the tree."""
    ordered = sorted(result.conflicts,
                     key=lambda item: (list(item.path), item.kind))
    id_map: dict[str, str] = {}
    for index, conflict in enumerate(ordered, start=1):
        new_id = f"C{index}"
        id_map[conflict.id] = new_id
        conflict.id = new_id
    result.conflicts = ordered
    for event in result.log:
        if event.get("action") == "conflict":
            event["conflictId"] = id_map[event["conflictId"]]
    _retag_tree(tree, id_map)


def _retag_tree(node: Any, id_map: dict[str, str]) -> None:
    if isinstance(node, dict):
        if "__unresolved__" in node:
            node["__unresolved__"] = id_map[node["__unresolved__"]]
        if "__conflict__" in node:
            node["__conflict__"] = id_map[node["__conflict__"]]
        for value in node.values():
            _retag_tree(value, id_map)
    elif isinstance(node, list):
        for value in node:
            _retag_tree(value, id_map)


def _apply_resolutions(node: Any, result: MergeResult) -> Any:
    """Replace resolved placeholders; keep open ones for the preview."""
    if isinstance(node, dict):
        if "__unresolved__" in node:
            conflict = result.get(node["__unresolved__"])
            if conflict.resolution is None:
                return dict(node)
            chosen = _resolved_value(conflict)
            return DELETED if chosen is DELETED else chosen
        if "__conflict__" in node:
            conflict = result.get(node["__conflict__"])
            if conflict.resolution is None:
                return dict(node)
            chosen = _resolved_entity(conflict)
            return DELETED if chosen is None else chosen
        rebuilt: dict = {}
        for key, value in node.items():
            child = _apply_resolutions(value, result)
            if child is not DELETED:
                rebuilt[key] = child
        return rebuilt
    if isinstance(node, list):
        rebuilt_list: list = []
        for value in node:
            child = _apply_resolutions(value, result)
            if child is not DELETED:
                rebuilt_list.append(child)
        return rebuilt_list
    return node


def _resolved_value(conflict: Conflict) -> Any:
    choice = conflict.resolution
    if choice == "delete":
        return DELETED
    selected = {"left": conflict.left, "right": conflict.right,
                "base": conflict.base}[choice]
    if selected is MISSING:
        return DELETED
    return selected


def _resolved_entity(conflict: Conflict) -> dict | None:
    choice = conflict.resolution
    if choice == "delete":
        return None
    selected = {"left": conflict.left, "right": conflict.right,
                "base": conflict.base}[choice]
    return None if selected is MISSING else selected


def _merge_project_name(result: MergeResult, base, left, right) -> Any:
    return merge_value(
        result, ["name"], "project.name",
        base.get("name"), left.get("name"), right.get("name"),
        entity_type="project",
    )


def _merge_entity_collection(
    result: MergeResult,
    base_items: list[dict],
    left_items: list[dict],
    right_items: list[dict],
    entity_type: str,
    path: list[str],
) -> list[dict]:
    base_idx, left_idx, right_idx = _index(base_items), _index(left_items), _index(right_items)
    merged_items: list[dict] = []
    for entity_id in sorted(set(base_idx) | set(left_idx) | set(right_idx)):
        entity_path = path + [entity_id]
        item = _merge_one_entity(
            result,
            base_idx.get(entity_id), left_idx.get(entity_id),
            right_idx.get(entity_id),
            entity_type, entity_path,
        )
        if item is not None and item is not DELETED:
            merged_items.append(item)
    return merged_items


def _merge_one_entity(
    result: MergeResult,
    base: dict | None,
    left: dict | None,
    right: dict | None,
    entity_type: str,
    path: list[str],
) -> dict | None:
    label = f"{entity_type}:{path[-1]}"

    if base is None:
        if left is not None and right is not None:
            if _entity_equal(left, right):
                result.record("auto_merge", path=list(path), source="both",
                              label=label, detail="concurrent_add_identical")
                chosen = left
            else:
                conflict = Conflict(
                    id=result.next_conflict_id(),
                    kind="concurrent_add",
                    entity_type=entity_type,
                    path=list(path), label=label,
                    base=MISSING, left=left, right=right,
                )
                result.add_conflict(conflict)
                return _conflict_entity_placeholder(
                    conflict, result, path,
                    left=left, right=right, base=None,
                )
        else:
            source = "left" if left is not None else "right"
            chosen = left or right
            result.record("auto_merge", path=list(path), source=source,
                          label=label, detail="added")
        return _build_entity(result, chosen, chosen, chosen,
                             entity_type, path)

    if left is None or right is None:
        if left is None and right is None:
            result.record("auto_merge", path=list(path), source="both",
                          label=label, detail="deleted")
            return DELETED
        if left is None:
            if _entity_equal(base, right):
                result.record("auto_merge", path=list(path), source="left",
                              label=label, detail="delete_wins_unchanged")
                return DELETED
            conflict = Conflict(
                id=result.next_conflict_id(), kind="delete_modify",
                entity_type=entity_type, path=list(path), label=label,
                base=base, left=DELETED, right=right,
            )
        else:
            if _entity_equal(base, left):
                result.record("auto_merge", path=list(path), source="right",
                              label=label, detail="delete_wins_unchanged")
                return DELETED
            conflict = Conflict(
                id=result.next_conflict_id(), kind="delete_modify",
                entity_type=entity_type, path=list(path), label=label,
                base=base, left=left, right=DELETED,
            )
        result.add_conflict(conflict)
        return _conflict_entity_placeholder(
            conflict, result, path,
            left=left, right=right, base=base,
        )

    if _entity_equal(base, left) and _entity_equal(base, right):
        return _build_entity(result, base, left, right,
                             entity_type, path)
    return _build_entity(result, base, left, right,
                         entity_type, path)


def _conflict_entity_placeholder(
    conflict: Conflict,
    result: MergeResult,
    path: list[str],
    left: dict | None,
    right: dict | None,
    base: dict | None,
) -> Any:
    preview = left or right or base
    if preview is None:
        return None
    return _build_entity(result, preview, preview, preview,
                         conflict.entity_type, path,
                         placeholder=conflict.id)


def _build_entity(
    result: MergeResult,
    base: dict,
    left: dict,
    right: dict,
    entity_type: str,
    path: list[str],
    placeholder: str | None = None,
) -> dict:
    name = merge_value(
        result, path + ["name"], f"{entity_type}:{path[-1]}.name",
        base["name"], left["name"], right["name"], entity_type,
    )
    params = _merge_nested_dict(
        result, path + ["params"],
        base["params"], left["params"], right["params"], entity_type,
    )
    entity: dict = {"id": path[-1], "name": name, "params": params}
    if entity_type == "track":
        entity["clips"] = _merge_entity_collection(
            result,
            base.get("clips", []), left.get("clips", []), right.get("clips", []),
            entity_type="clip", path=path + ["clips"],
        )
    if placeholder is not None:
        entity["__conflict__"] = placeholder
    return entity


def _entity_equal(left: dict | None, right: dict | None) -> bool:
    if left is None or right is None:
        return left is right
    return equal(left, right)
