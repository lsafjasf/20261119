from __future__ import annotations

from dataclasses import asdict
from fractions import Fraction

from .core import Project, RenderedEvent, TimelineError
from .flatten import flatten
from .models import T


def _event_key(event: RenderedEvent, flattened: bool) -> tuple:
    full_name = event.source_clip if flattened else "/".join(event.path + (event.source_clip,))
    return (full_name, event.kind, event.root_time, event.source_time)


def export_timeline(project: Project, root_name: str) -> dict:
    """导出主时间线。

    导出前先拒绝循环引用；随后分别渲染嵌套形态与展开形态，
    逐事件比对画面/音频的根时间位置，确保时间映射完全一致。
    """
    project.ensure_acyclic(root_name)  # 循环引用必须在导出前被拒绝

    nested_events = project.render(root_name)
    flat_sequence = flatten(project, root_name)
    flat_project = Project({flat_sequence.name: flat_sequence})
    flat_events = flat_project.render(flat_sequence.name)

    nested_keys = sorted(_event_key(e, False) for e in nested_events)
    flat_keys = sorted(_event_key(e, True) for e in flat_events)
    if nested_keys != flat_keys:
        raise TimelineError(
            "展开前后事件位置不一致:\n"
            f"  嵌套: {nested_keys}\n  展开: {flat_keys}"
        )

    return {
        "root": root_name,
        "duration": _sequence_span(project, root_name),
        "events": [_event_to_dict(e) for e in nested_events],
        "flattened_sequence": flat_sequence.name,
        "consistency_checked": True,
        "event_count": len(nested_events),
    }


def _sequence_span(project: Project, root_name: str) -> Fraction:
    sequence = project.get(root_name)
    ends = [clip.end for clip in sequence.clips] + [ref.end for ref in sequence.refs]
    return max(ends) if ends else Fraction(0)


def _event_to_dict(event: RenderedEvent) -> dict:
    data = asdict(event)
    data["root_time"] = _num(data["root_time"])
    data["source_time"] = _num(data["source_time"])
    data["path"] = list(event.path)
    return data


def _num(value: Fraction) -> int | float:
    value = T(value)
    return int(value) if value.denominator == 1 else float(value)
