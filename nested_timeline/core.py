from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .models import Clip, NestedRef, Sequence


class TimelineError(Exception):
    """时间线结构错误（缺失序列、循环引用等）。"""


@dataclass(frozen=True)
class RenderedEvent:
    """渲染后事件：已换算到根（主时间线）坐标系。"""

    kind: str  # "video" 或 "audio"
    source_clip: str
    root_time: Fraction
    source_time: Fraction  # 源媒体时间
    path: tuple[str, ...]  # 根 -> 叶子经过的嵌套引用名


class Project:
    def __init__(self, sequences: dict[str, Sequence] | None = None) -> None:
        self.sequences: dict[str, Sequence] = dict(sequences or {})

    def add(self, sequence: Sequence) -> Sequence:
        self.sequences[sequence.name] = sequence
        return sequence

    def get(self, name: str) -> Sequence:
        try:
            return self.sequences[name]
        except KeyError:
            raise TimelineError(f"未知序列: {name!r}") from None

    def detect_cycle(self, root_name: str) -> list[str]:
        """从 root_name 出发做 DFS，无环返回 []；有环返回构成环的引用名链。"""
        stack: list[str] = []
        on_stack: set[str] = set()

        def visit(seq_name: str) -> list[str] | None:
            self.get(seq_name)  # 缺失序列直接抛 TimelineError
            stack.append(seq_name)
            on_stack.add(seq_name)
            for ref in self.sequences[seq_name].refs:
                if ref.sequence in on_stack:
                    idx = stack.index(ref.sequence)
                    return stack[idx:] + [ref.sequence]
                found = visit(ref.sequence)
                if found is not None:
                    return found
            on_stack.remove(seq_name)
            stack.pop()
            return None

        return visit(root_name) or []

    def ensure_acyclic(self, root_name: str) -> None:
        cycle = self.detect_cycle(root_name)
        if cycle:
            raise TimelineError("检测到循环引用，导出被拒绝: " + " -> ".join(cycle))

    def render(self, root_name: str) -> list[RenderedEvent]:
        """递归渲染主时间线，返回根坐标系下的全部画面/音频事件。"""
        self.ensure_acyclic(root_name)
        events: list[RenderedEvent] = []
        # local_to_root: 当前序列局部时间 -> 根时间的仿射映射 root = m*local + b
        self._render_sequence(root_name, Fraction(1), Fraction(0), None, events)
        events.sort(key=lambda e: (e.root_time, e.kind, e.source_clip))
        return events

    def _render_sequence(
        self,
        seq_name: str,
        m: Fraction,
        b: Fraction,
        window: tuple[Fraction, Fraction] | None,
        events: list[RenderedEvent],
        path: tuple[str, ...] = (),
    ) -> None:
        sequence = self.get(seq_name)
        for clip in sequence.clips:
            self._render_clip(clip, m, b, window, events, path)
        for ref in sequence.refs:
            self._render_ref(ref, m, b, window, events, path)

    def _render_clip(
        self,
        clip: Clip,
        m: Fraction,
        b: Fraction,
        window: tuple[Fraction, Fraction] | None,
        events: list[RenderedEvent],
        path: tuple[str, ...],
    ) -> None:
        for kind, source_events in (
            ("video", clip.video_events),
            ("audio", clip.audio_events),
        ):
            for source_time in source_events:
                local = clip.start + (source_time - clip.source_in) / clip.rate
                if not (clip.start <= local <= clip.end):
                    continue  # 事件落在本切片取用的媒体区间之外
                if window is not None and not (window[0] <= local <= window[1]):
                    continue  # 事件落在父级嵌套的可见窗口之外
                events.append(
                    RenderedEvent(
                        kind=kind,
                        source_clip=clip.name,
                        root_time=m * local + b,
                        source_time=source_time,
                        path=path,
                    )
                )

    def _render_ref(
        self,
        ref: NestedRef,
        m: Fraction,
        b: Fraction,
        window: tuple[Fraction, Fraction] | None,
        events: list[RenderedEvent],
        path: tuple[str, ...],
    ) -> None:
        child = self.get(ref.sequence)
        # 父局部 parent = start + (child - source_in)*rate
        # 复合到根: root = m*parent + b
        #         = (m*rate)*child + (m*(start - source_in*rate) + b)
        child_m = m * ref.rate
        child_b = m * (ref.start - ref.source_in * ref.rate) + b
        # 可见窗口换算到子序列局部坐标，再与引用自身的取用区间求交。
        # 上层 window 是当前序列（父）局部坐标，需经 parent->child 逆映射：
        # child = source_in + (parent - start)/rate
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
        self._render_sequence(
            child.name, child_m, child_b, local_window, events, path + (ref.name,)
        )
