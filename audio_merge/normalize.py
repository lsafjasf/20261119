"""Project loading, schema validation and deterministic normalization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ProjectFormatError(ValueError):
    """Raised when a project document fails structural validation."""


_SCALAR_TYPES = (str, int, float, bool, type(None))


def _validate_param_value(value: Any, path: str) -> None:
    if isinstance(value, bool) or isinstance(value, _SCALAR_TYPES):
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ProjectFormatError(f"{path}: param keys must be strings")
            _validate_param_value(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        # Lists are opaque leaf values: they are replaced wholesale.
        for index, child in enumerate(value):
            _validate_param_value(child, f"{path}[{index}]")
        return
    raise ProjectFormatError(f"{path}: unsupported value type {type(value).__name__}")


def _validate_params(params: Any, path: str) -> dict:
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise ProjectFormatError(f"{path}: params must be an object")
    for key, value in params.items():
        if not isinstance(key, str):
            raise ProjectFormatError(f"{path}: param keys must be strings")
        _validate_param_value(value, f"{path}.{key}")
    return dict(params)


def _validate_entity(entity: Any, kind: str, path: str) -> dict:
    if not isinstance(entity, dict):
        raise ProjectFormatError(f"{path}: {kind} must be an object")
    entity_id = entity.get("id")
    if not isinstance(entity_id, str) or not entity_id:
        raise ProjectFormatError(f"{path}: {kind} requires a non-empty string id")
    name = entity.get("name")
    if name is not None and not isinstance(name, str):
        raise ProjectFormatError(f"{path}/{entity_id}: name must be a string")
    params = _validate_params(entity.get("params"), f"{path}/{entity_id}.params")
    return {"id": entity_id, "name": name, "params": params, "raw": entity}


def validate_project(doc: Any) -> dict:
    """Validate a decoded project document and return its canonical parts."""
    if not isinstance(doc, dict):
        raise ProjectFormatError("project root must be an object")
    tracks_raw = doc.get("tracks", [])
    if not isinstance(tracks_raw, list):
        raise ProjectFormatError("project.tracks must be a list")

    tracks: list[dict] = []
    track_ids: set[str] = set()
    for track_raw in tracks_raw:
        track = _validate_entity(track_raw, "track", "tracks")
        track_id = track["id"]
        if track_id in track_ids:
            raise ProjectFormatError(f"duplicate track id: {track_id}")
        track_ids.add(track_id)

        clips_raw = track_raw.get("clips", [])
        if not isinstance(clips_raw, list):
            raise ProjectFormatError(f"tracks/{track_id}: clips must be a list")
        clips: list[dict] = []
        clip_ids: set[str] = set()
        for clip_raw in clips_raw:
            clip = _validate_entity(clip_raw, "clip", f"tracks/{track_id}/clips")
            if clip["id"] in clip_ids:
                raise ProjectFormatError(
                    f"duplicate clip id in track {track_id}: {clip['id']}"
                )
            clip_ids.add(clip["id"])
            clips.append(clip)
        tracks.append({"entity": track, "clips": clips})

    name = doc.get("name")
    if name is not None and not isinstance(name, str):
        raise ProjectFormatError("project.name must be a string")
    return {"name": name, "tracks": tracks}


def _canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, float):
        # Round-trip through repr so equivalent floats render identically.
        return float(repr(value))
    return value


def normalize_project(doc: Any) -> dict:
    """Return a structurally normalized, key-sorted project document."""
    validated = validate_project(doc)
    normalized_tracks = []
    for track_bundle in sorted(validated["tracks"], key=lambda item: item["entity"]["id"]):
        track = track_bundle["entity"]
        normalized_clips = []
        for clip in sorted(track_bundle["clips"], key=lambda item: item["id"]):
            normalized_clips.append(
                {
                    "id": clip["id"],
                    "name": clip["name"],
                    "params": _canonical_value(clip["params"]),
                }
            )
        normalized_tracks.append(
            {
                "id": track["id"],
                "name": track["name"],
                "params": _canonical_value(track["params"]),
                "clips": normalized_clips,
            }
        )
    return {
        "version": 1,
        "name": validated["name"],
        "tracks": normalized_tracks,
    }


def canonical_json(doc: Any) -> str:
    """Serialize a document deterministically."""
    return json.dumps(
        _canonical_value(doc),
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    ) + "\n"


def load_project(path: str | Path) -> dict:
    """Load and validate a project JSON file."""
    source = Path(path)
    try:
        doc = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProjectFormatError(f"{source}: invalid JSON ({exc})") from exc
    validate_project(doc)
    return doc
