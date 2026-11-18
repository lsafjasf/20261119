from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction


def T(value: int | float | str | Fraction) -> Fraction:
    """把任意时间值归一化为精确分数时间。"""
    return value if isinstance(value, Fraction) else Fraction(value)


@dataclass
class Clip:
    """叶子片段：一段具体媒体上的画面/音频切片。

    start/duration 是片段在所属序列局部时间线上的位置与时长；
    source_in 是该切片在源媒体内的入点。
    媒体事件（标记点）以源媒体时间为基准记录。
    """

    name: str
    start: Fraction
    duration: Fraction
    source_in: Fraction = Fraction(0)
    rate: Fraction = Fraction(1)
    video_events: list[Fraction] = field(default_factory=list)
    audio_events: list[Fraction] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.start = T(self.start)
        self.duration = T(self.duration)
        self.source_in = T(self.source_in)
        self.rate = T(self.rate)
        self.video_events = [T(e) for e in self.video_events]
        self.audio_events = [T(e) for e in self.audio_events]
        if self.duration < 0:
            raise ValueError(f"片段 {self.name!r} 时长不能为负")
        if self.rate <= 0:
            raise ValueError(f"片段 {self.name!r} 播放速率必须为正数")

    @property
    def end(self) -> Fraction:
        return self.start + self.duration


@dataclass
class NestedRef:
    """对另一个序列（子序列）的嵌套引用。

    时间映射（子序列局部时间 child -> 父序列局部时间 parent）：
        parent = start + (child - source_in) * rate

    - start:     子序列 source_in 这一时刻落在父序列上的位置；
    - source_in: 子序列自身时间轴上的入点（允许为负，即负偏移）；
    - rate:      父序列相对子序列的播放速率，2 表示子序列二倍速。

    负偏移时 source_in < 0：子序列的 0 时刻映射到 start - source_in*rate，
    即子序列开头会出现在 start 之前（前置手柄），映射仍为同一条连续仿射直线。
    """

    name: str
    sequence: str
    start: Fraction
    duration: Fraction
    source_in: Fraction = Fraction(0)
    rate: Fraction = Fraction(1)

    def __post_init__(self) -> None:
        self.start = T(self.start)
        self.duration = T(self.duration)
        self.source_in = T(self.source_in)
        self.rate = T(self.rate)
        if self.duration < 0:
            raise ValueError(f"嵌套 {self.name!r} 时长不能为负")
        if self.rate <= 0:
            raise ValueError(f"嵌套 {self.name!r} 速率必须为正数")

    def child_to_parent(self, child_time: Fraction) -> Fraction:
        child_time = T(child_time)
        return self.start + (child_time - self.source_in) * self.rate

    def parent_to_child(self, parent_time: Fraction) -> Fraction:
        parent_time = T(parent_time)
        return self.source_in + (parent_time - self.start) / self.rate

    @property
    def source_out(self) -> Fraction:
        """父序列上该引用结束位置对应的子序列时间。"""
        return self.source_in + self.duration / self.rate

    @property
    def end(self) -> Fraction:
        return self.start + self.duration


@dataclass
class Sequence:
    """一条序列时间线，由叶子片段和嵌套引用混合组成。"""

    name: str
    clips: list[Clip] = field(default_factory=list)
    refs: list[NestedRef] = field(default_factory=list)

    def find_clip(self, name: str) -> Clip:
        for clip in self.clips:
            if clip.name == name:
                return clip
        raise KeyError(f"序列 {self.name!r} 中没有片段 {name!r}")

    def find_ref(self, name: str) -> NestedRef:
        for ref in self.refs:
            if ref.name == name:
                return ref
        raise KeyError(f"序列 {self.name!r} 中没有嵌套 {name!r}")
