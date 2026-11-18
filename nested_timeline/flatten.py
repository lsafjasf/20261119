from __future__ import annotations

from fractions import Fraction

from .core import Project
from .models import Clip, NestedRef, Sequence


def flatten(project: Project, root_name: str) -> Sequence:
    """把主时间线展开为只含叶子片段的扁平序列。

    展开是深拷贝式的：返回的新片段与原子序列没有共享状态，
    之后单独修改展开结果不会影响任何子序列，反之亦然。
    每个叶子片段的位置/时长/速率按逐级仿射映射换算到根坐标系。
    """
    project.ensure_acyclic(root_name)
    flat = Sequence(name=f"{root_name}__flattened")
    _walk(
        project,
        project.get(root_name),
        m=Fraction(1),
        b=Fraction(0),
        window=None,
        path=(),
        out=flat,
    )
    flat.clips.sort(key=lambda c: c.start)
    return flat


def _walk(
    project: Project,
    sequence: Sequence,
    m: Fraction,
    b: Fraction,
    window: tuple[Fraction, Fraction] | None,
    path: tuple[str, ...],
    out: Sequence,
) -> None:
    for clip in sequence.clips:
        _flatten_clip(clip, m, b, window, path, out)
    for ref in sequence.refs:
        _flatten_ref(project, ref, m, b, window, path, out)


def _flatten_clip(
    clip: Clip,
    m: Fraction,
    b: Fraction,
    window: tuple[Fraction, Fraction] | None,
    path: tuple[str, ...],
    out: Sequence,
) -> None:
    local_start, local_end = clip.start, clip.end
    if window is not None:
        local_start = max(local_start, window[0])
        local_end = min(local_end, window[1])
    if local_start >= local_end:
        return
    local_duration = local_end - local_start
    source_in = clip.source_in + (local_start - clip.start) * clip.rate
    full_name = "/".join(path + (clip.name,))
    out.clips.append(
        Clip(
            name=full_name,
            start=m * local_start + b,
            duration=m * local_duration,
            source_in=source_in,
            rate=clip.rate / m,
            video_events=list(clip.video_events),
            audio_events=list(clip.audio_events),
        )
    )


def _flatten_ref(
    project: Project,
    ref: NestedRef,
    m: Fraction,
    b: Fraction,
    window: tuple[Fraction, Fraction] | None,
    path: tuple[str, ...],
    out: Sequence,
) -> None:
    child_m = m * ref.rate
    child_b = m * (ref.start - ref.source_in * ref.rate) + b
    local_window = (ref.source_in, ref.source_out)
    if window is not None:
        win_lo = ref.source_in + (window[0] - ref.start) / ref.rate
        win_hi = ref.source_in + (window[1] - ref.start) / ref.rate
        local_window = (
            max(local_window[0], win_lo),
            min(local_window[1], win_hi),
        )
    if local_window[0] > local_window[1]:
        return
    _walk(
        project,
        project.get(ref.sequence),
        child_m,
        child_b,
        local_window,
        path + (ref.name,),
        out,
    )
