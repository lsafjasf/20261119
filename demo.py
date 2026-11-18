from __future__ import annotations

from fractions import Fraction

from nested_timeline import (
    Project,
    TimelineError,
    export_timeline,
    flatten,
    samples,
)


def fmt(value) -> str:
    value = value if isinstance(value, Fraction) else Fraction(value)
    return str(value) if value.denominator != 1 else str(int(value))


def show_events(title: str, events) -> None:
    print(f"  {title}（共 {len(events)} 个事件）")
    for event in events:
        path = "/".join(event.path + (event.source_clip,))
        print(
            f"    {event.kind:5s} 根时间={fmt(event.root_time):>4}  "
            f"源时间={fmt(event.source_time):>4}  路径={path}"
        )


def section(title: str) -> None:
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def demo_speed() -> None:
    section("样本一：子序列二倍速（rate=2）")
    project = samples.build_speed_sample()
    ref = project.get("main_speed").find_ref("R1")
    print("映射公式: parent = start + (child - source_in) * rate = 4 + 2*child")
    for child_time in (0, 1, 2, Fraction(5, 2)):
        print(f"  子序列 {fmt(child_time)}s  ->  主时间线 {fmt(ref.child_to_parent(child_time))}s")
    for parent_time in (4, 6, 9):
        print(f"  主时间线 {parent_time}s  ->  子序列 {fmt(ref.parent_to_child(parent_time))}s")
    show_events("渲染事件", project.render("main_speed"))


def demo_negative_offset() -> None:
    section("样本二：负偏移（source_in=-2）")
    project = samples.build_negative_offset_sample()
    ref = project.get("main_negative").find_ref("R2")
    print("映射公式: parent = 2 + (child - (-2))*1 = child + 4")
    for child_time in (-2, 0, 3, 6, 8):
        print(f"  子序列 {fmt(child_time)}s  ->  主时间线 {fmt(ref.child_to_parent(child_time))}s")
    print("  可见窗口 child ∈ [-2, 8)，其中 [-2,0) 为无内容的前置手柄区")
    show_events("渲染事件", project.render("main_negative"))
def demo_two_level() -> None:
    section("样本三：双层嵌套（逐级换算）")
    project = samples.build_two_level_sample()
    outer = project.get("main_nested").find_ref("R_outer")
    inner = project.get("childC").find_ref("R_inner")
    print("内层 R_inner: childC = 1 + 2*grandG")
    print("外层 R_outer: main    = 10 + (childC - 1)*1")
    print("复合结果:     main    = 10 + (1 + 2*grandG - 1) = 10 + 2*grandG")
    for grand_time in (0, 1, 2):
        child_time = inner.child_to_parent(grand_time)
        main_time = outer.child_to_parent(child_time)
        print(
            f"  grandG {fmt(grand_time)}s -> childC {fmt(child_time)}s "
            f"-> main {fmt(main_time)}s"
        )
    show_events("渲染事件", project.render("main_nested"))


def demo_cycle() -> None:
    section("循环引用：导出前必须被拒绝")
    project = samples.build_cycle_sample()
    print("引用关系: cycX -> cycY -> cycX")
    try:
        export_timeline(project, "cycX")
    except TimelineError as exc:
        print(f"  导出被拒绝 -> {exc}")


def demo_flatten_and_export() -> None:
    section("展开嵌套 + 渲染导出（前后事件位置一致性）")
    for root_name in ("main_speed", "main_negative", "main_nested"):
        project = (
            samples.build_speed_sample()
            if root_name == "main_speed"
            else samples.build_negative_offset_sample()
            if root_name == "main_negative"
            else samples.build_two_level_sample()
        )
        result = export_timeline(project, root_name)
        flat = flatten(project, root_name)
        print(f"\n[{root_name}] 导出 {result['event_count']} 个事件，"
              f"一致性校验={result['consistency_checked']}")
        print("  展开后的扁平片段:")
        for clip in flat.clips:
            print(
                f"    {clip.name}: start={fmt(clip.start)} "
                f"duration={fmt(clip.duration)} rate={fmt(clip.rate)}"
            )


def demo_edit_propagation() -> None:
    section("编辑语义：父级编辑反映到子片段且不破坏子序列内部；展开后可独立修改")
    project = samples.build_speed_sample()
    main = project.get("main_speed")
    child = project.get("childA")

    before = [e.root_time for e in project.render("main_speed")]
    main.find_ref("R1").start = 8  # 父序列上整体平移嵌套引用
    after = [e.root_time for e in project.render("main_speed")]
    shift = after[0] - before[0]
    print(f"父级把 R1.start 4->8：全部根时间平移 {fmt(shift)}s，"
          f"子序列内部片段位置仍为 start={fmt(child.clips[0].start)}（内部结构未破坏）")

    flat = flatten(project, "main_speed")
    flat.clips[0].start += 100  # 展开后的独立修改
    still = child.clips[0].start
    print(f"展开后单独平移扁平片段 +100s：原子序列片段 start 仍为 {fmt(still)}，互不影响")


if __name__ == "__main__":
    demo_speed()
    demo_negative_offset()
    demo_two_level()
    demo_cycle()
    demo_flatten_and_export()
    demo_edit_propagation()
    print("\n全部演示完成。")
