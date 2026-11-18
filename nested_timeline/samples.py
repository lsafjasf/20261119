from __future__ import annotations

from fractions import Fraction

from .core import Project
from .models import Clip, NestedRef, Sequence


def build_speed_sample() -> Project:
    """样本一：子序列二倍速嵌套。

    子序列 childA 局部 [0,10]，主时间线以 rate=2 取用其前 2.5 秒，
    父序列上的引用时长因此只有 5 秒。
    """
    child_a = Sequence(
        name="childA",
        clips=[
            Clip(
                name="A_clip",
                start=0,
                duration=10,
                video_events=[0, 1, 2],
                audio_events=[Fraction(1, 2), 2],
            )
        ],
    )
    main = Sequence(
        name="main_speed",
        refs=[
            NestedRef(
                name="R1",
                sequence="childA",
                start=4,
                duration=5,
                source_in=0,
                rate=2,
            )
        ],
    )
    return Project({"childA": child_a, "main_speed": main})


def build_negative_offset_sample() -> Project:
    """样本二：负偏移（source_in = -2）。

    映射 parent = start + (child - source_in)*rate = child + 4。
    引用从 parent=2 开始，但子序列 0 时刻落在 parent=4；
    parent [2,4) 对应子序列局部 [-2,0) 的前置手柄区，没有任何片段内容。
    """
    child_b = Sequence(
        name="childB",
        clips=[
            Clip(
                name="B_clip",
                start=0,
                duration=6,
                video_events=[0, 3, 6],
                audio_events=[Fraction(3, 2)],
            )
        ],
    )
    main = Sequence(
        name="main_negative",
        refs=[
            NestedRef(
                name="R2",
                sequence="childB",
                start=2,
                duration=10,
                source_in=-2,
                rate=1,
            )
        ],
    )
    return Project({"childB": child_b, "main_negative": main})


def build_two_level_sample() -> Project:
    """样本三：双层嵌套。

    grandG --(rate=2)--> childC --(rate=1, source_in=1)--> main_nested。
    逐级换算：grand 局部时间先经内层引用映射到 childC，
    再经外层引用映射到主时间线。
    """
    grand = Sequence(
        name="grandG",
        clips=[
            Clip(
                name="G_clip",
                start=0,
                duration=4,
                video_events=[0, 2, 4],
                audio_events=[1],
            )
        ],
    )
    child_c = Sequence(
        name="childC",
        refs=[
            NestedRef(
                name="R_inner",
                sequence="grandG",
                start=1,
                duration=4,
                source_in=0,
                rate=2,
            )
        ],
    )
    main = Sequence(
        name="main_nested",
        refs=[
            NestedRef(
                name="R_outer",
                sequence="childC",
                start=10,
                duration=2,
                source_in=1,
                rate=1,
            )
        ],
    )
    return Project(
        {"grandG": grand, "childC": child_c, "main_nested": main}
    )


def build_cycle_sample() -> Project:
    """循环引用样本：cycX -> cycY -> cycX，导出前必须被拒绝。"""
    seq_x = Sequence(
        name="cycX",
        refs=[NestedRef("loop_to_y", "cycY", start=0, duration=4)],
    )
    seq_y = Sequence(
        name="cycY",
        refs=[NestedRef("loop_to_x", "cycX", start=0, duration=4)],
    )
    return Project({"cycX": seq_x, "cycY": seq_y})
