"""Tile-level dependencies and extraction eligibility."""

from __future__ import annotations

from dataclasses import dataclass, field

from .layout import slice_tile_ranges
from .models import Dependency, IDR_TYPES, Slice, StreamInfo, TileMetric
from .parser import build_tile_metrics


@dataclass(slots=True)
class Condition:
    key: str
    label: str
    passed: bool
    detail: str
    required_for_local: bool = True


@dataclass(slots=True)
class Analysis:
    conditions: list[Condition]
    dependencies: list[Dependency]
    metrics: list[TileMetric]
    frame_index: int
    frame_slices: list[Slice]
    local_allowed: bool
    blocked_reasons: list[str] = field(default_factory=list)

    @property
    def cross_tile_dependencies(self) -> list[Dependency]:
        return [edge for edge in self.dependencies if edge.source != edge.target]


def frame_slices(info: StreamInfo, frame_index: int = 0) -> list[Slice]:
    frames: dict[int, list[Slice]] = {}
    for slice_ in info.slices:
        frames.setdefault(slice_.au_index, []).append(slice_)
    ordered = [frames[key] for key in sorted(frames)]
    if not ordered:
        return []
    index = frame_index if frame_index >= 0 else len(ordered) + frame_index
    if index < 0 or index >= len(ordered):
        raise IndexError(f"frame {frame_index} does not exist ({len(ordered)} frames)")
    return ordered[index]


def frame_count(info: StreamInfo) -> int:
    return len({slice_.au_index for slice_ in info.slices})


def analyze(info: StreamInfo, frame_index: int = 0) -> Analysis:
    if info.layout is None or info.sps is None or info.pps is None:
        raise ValueError("stream has no usable tile layout")
    slices = frame_slices(info, frame_index)
    conditions = _conditions(info, slices)
    dependencies = _dependencies(info, slices)
    blocked = [
        f"{item.label}: {item.detail}"
        for item in conditions
        if item.required_for_local and not item.passed
    ]
    return Analysis(
        conditions=conditions,
        dependencies=dependencies,
        metrics=build_tile_metrics(info),
        frame_index=frame_index,
        frame_slices=slices,
        local_allowed=not blocked,
        blocked_reasons=blocked,
    )


def _conditions(info: StreamInfo, slices: list[Slice]) -> list[Condition]:
    pps = info.pps
    sps = info.sps
    assert pps is not None and sps is not None and info.layout is not None
    layout = info.layout

    tile_count_ok = bool(pps.tiles_enabled and len(layout.tiles) > 1)
    loop_filter_off = (
        not pps.tiles_enabled or not pps.loop_filter_across_tiles_enabled
    )
    if not pps.tiles_enabled:
        loop_filter_detail = "未启用瓦片布局，不存在跨瓦片滤波"
    elif loop_filter_off:
        loop_filter_detail = (
            "loop_filter_across_tiles_enabled_flag=0，边缘像素不读取邻瓦片"
        )
    else:
        loop_filter_detail = (
            "loop_filter_across_tiles_enabled_flag=1；"
            "边缘去块/SAO 需要邻瓦片像素"
        )
    dependent_off = not pps.dependent_slice_segments_enabled and not any(
        slice_.dependent for slice_ in slices
    )
    wpp_off = not pps.entropy_coding_sync_enabled
    intra_only = bool(slices) and all(
        slice_.slice_type == 2 and slice_.nal.nal_type in IDR_TYPES
        for slice_ in slices
    )

    split_ok, split_detail = _slice_alignment(info, slices)
    parameter_errors = [warning for warning in info.warnings if "NAL" in warning]
    parse_ok = not parameter_errors and bool(slices)
    first_present = any(slice_.first_in_pic for slice_ in slices)
    parameter_sets_consistent = bool(slices) and all(
        slice_.pps_id == pps.pps_id
        and info.pps_by_id.get(slice_.pps_id) is not None
        and info.pps_by_id[slice_.pps_id].sps_id == sps.sps_id
        for slice_ in slices
    )

    return [
        Condition(
            "tiles",
            "瓦片布局",
            tile_count_ok,
            (
                f"{layout.num_columns} 列 × {layout.num_rows} 行，共 {len(layout.tiles)} 个瓦片"
                if tile_count_ok
                else "PPS 未启用多瓦片布局，无法按瓦片独立提取"
            ),
        ),
        Condition(
            "loop_filter",
            "跨瓦片环路滤波关闭",
            loop_filter_off,
            loop_filter_detail,
        ),
        Condition(
            "dependent_slices",
            "无依赖切片段",
            dependent_off,
            (
                "dependent_slice_segments_enabled_flag=0，当前帧无依赖切片"
                if dependent_off
                else "存在依赖切片段，后续切片需要前一片段的 CABAC/头状态"
            ),
        ),
        Condition(
            "wpp",
            "无 WPP 行依赖",
            wpp_off,
            (
                "entropy_coding_sync_enabled_flag=0"
                if wpp_off
                else "启用 WPP；CTB 行之间有上下文依赖，当前提取器不宣称瓦片独立"
            ),
        ),
        Condition(
            "intra",
            "当前帧全为 IDR I 切片",
            intra_only,
            (
                "无跨帧/跨瓦片运动补偿参考"
                if intra_only
                else "存在 P/B 或非 IDR I 切片；可能需要瓦片外参考像素，不能据此证明局部独立"
            ),
        ),
        Condition(
            "slice_alignment",
            "切片与瓦片边界可隔离",
            split_ok,
            split_detail,
        ),
        Condition(
            "parse",
            "参数集与切片头完整",
            parse_ok,
            (
                "SPS/PPS/slice header 均已解析"
                if parse_ok
                else "; ".join(parameter_errors) or "没有可分析的切片"
            ),
        ),
        Condition(
            "parameter_sets",
            "当前帧参数集一致",
            parameter_sets_consistent,
            (
                "帧内切片使用同一 SPS/PPS 组合"
                if parameter_sets_consistent
                else "帧内存在参数集切换，当前报告不能安全合并瓦片统计"
            ),
        ),
        Condition(
            "first_slice",
            "帧首切片存在",
            first_present,
            (
                "可用首个切片或以仅头部占位切片表达缺失瓦片"
                if first_present
                else "缺少 first_slice_segment_in_pic_flag=1 的切片"
            ),
        ),
        Condition(
            "slice_header_extension",
            "无未知切片头扩展",
            not pps.slice_header_extension_present,
            (
                "slice_segment_header_extension_present_flag=0"
                if not pps.slice_header_extension_present
                else "存在未解释的切片头扩展，无法安全重写单瓦片切片"
            ),
        ),
    ]


def _slice_alignment(info: StreamInfo, slices: list[Slice]) -> tuple[bool, str]:
    assert info.layout is not None
    ordered = sorted(
        slices, key=lambda item: info.layout.rs_to_ts[item.address]
    )
    addresses = [slice_.address for slice_ in ordered]
    for index, slice_ in enumerate(ordered):
        start_ts = info.layout.rs_to_ts[slice_.address]
        end = (
            info.layout.rs_to_ts[ordered[index + 1].address]
            if index + 1 < len(ordered)
            else len(info.layout.ts_to_rs)
        )
        start_tile = info.layout.tile_for_ts(start_ts)
        covered = [
            tile
            for tile in info.layout.tiles
            if max(start_ts, tile.ts_start) < min(end, tile.ts_end)
        ]
        streams = len(slice_.entry_points) + 1
        if len(covered) > 1:
            first_tile = covered[0]
            if start_ts != first_tile.ts_start:
                return (
                    False,
                    f"切片 @TS{start_ts} 从瓦片中间开始且跨瓦片，"
                    "无法只保留完整瓦片子流",
                )
            if streams != len(covered):
                return (
                    False,
                    f"切片 @TS{start_ts} 覆盖 {len(covered)} 个瓦片，"
                    f"但入口点对应 {streams} 个子流，映射不唯一",
                )
        elif streams != 1:
            return (
                False,
                f"切片 @TS{start_ts} 位于单个瓦片内，却有 {streams} 个子流",
            )
        if covered[0].id != start_tile.id:
            return False, f"切片 @TS{slice_.address} 的瓦片映射不一致"
    if len(set(addresses)) != len(addresses):
        return False, "同一帧内存在重复 slice_segment_address"
    counts = slice_tile_ranges(info.layout, slices)
    missing = [tile_id for tile_id, count in counts.items() if count == 0]
    if missing:
        return False, f"瓦片 {missing} 没有切片覆盖，不能做完整像素核验"
    return (
        True,
        "每个子流均从完整瓦片边界开始；可重写为单瓦片切片，"
        "缺失瓦片用头部占位",
    )


def _dependencies(info: StreamInfo, slices: list[Slice]) -> list[Dependency]:
    pps = info.pps
    assert pps is not None and info.layout is not None
    edges: list[Dependency] = []

    if pps.loop_filter_across_tiles_enabled:
        for tile in info.layout.tiles:
            left = tile.id - 1 if tile.column > 0 else None
            top = tile.id - info.layout.num_columns if tile.row > 0 else None
            for source in (left, top):
                if source is not None:
                    edges.append(
                        Dependency(
                            source=source,
                            target=tile.id,
                            kind="loop-filter",
                            detail="跨瓦片去块/SAO 需要邻瓦片边缘像素",
                        )
                    )

    if pps.entropy_coding_sync_enabled:
        for tile in info.layout.tiles:
            edges.append(
                Dependency(
                    source=tile.id,
                    target=tile.id,
                    kind="wpp-row-chain",
                    detail="瓦片内部 CTB 行依赖上一行 CABAC 上下文",
                )
            )

    if any(slice_.slice_type != 2 for slice_ in slices):
        for tile in info.layout.tiles:
            edges.append(
                Dependency(
                    source=tile.id,
                    target=tile.id,
                    kind="picture-reference",
                    detail="非全 I 切片，可能存在跨帧/跨瓦片参考",
                )
            )

    previous_tile = None
    for slice_ in sorted(slices, key=lambda item: item.address):
        if slice_.dependent and previous_tile is not None:
            edges.append(
                Dependency(
                    source=previous_tile,
                    target=slice_.tile_id,
                    kind="dependent-slice",
                    detail=f"slice_segment_address={slice_.address}",
                )
            )
        elif slice_.dependent:
            edges.append(
                Dependency(
                    source=slice_.tile_id,
                    target=slice_.tile_id,
                    kind="dependent-slice",
                    detail=f"slice_segment_address={slice_.address}",
                )
            )
        previous_tile = slice_.tile_id

    if not edges:
        for tile in info.layout.tiles:
            edges.append(
                Dependency(
                    source=tile.id,
                    target=tile.id,
                    kind="intra-tile",
                    detail="仅自身内部 CTB/切片依赖",
                )
            )
    return edges
