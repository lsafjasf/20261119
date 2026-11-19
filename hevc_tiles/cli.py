"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analysis import analyze
from .decode import DecodeError, verify_tiles
from .layout import slice_tile_ranges
from .parser import parse_stream
from .report import generate_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hevc-tiles",
        description="解析 HEVC 瓦片布局，并核验可独立解码瓦片的局部像素。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect", help="显示瓦片布局、入口点、切片数与平均码量")
    inspect.add_argument("input", type=Path)
    inspect.add_argument("--frame", type=int, default=0)
    inspect.add_argument("--json", action="store_true", help="输出机器可读 JSON")

    report = sub.add_parser("report", help="生成自包含 HTML 报告与缺失掩码图")
    report.add_argument("input", type=Path)
    report.add_argument(
        "--available",
        default="all",
        help="可用瓦片，例如 0,2 或 all（默认 all）",
    )
    report.add_argument("--frame", type=int, default=0)
    report.add_argument("--output", type=Path, help="输出目录，默认 out/<文件名>")
    report.add_argument("--ffmpeg", help="ffmpeg 可执行文件路径")
    report.add_argument(
        "--no-decode",
        action="store_true",
        help="只解析并写布局报告，不执行完整/局部像素核验",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if getattr(args, "frame", 0) < 0:
            raise ValueError("--frame 必须是非负整数")
        info = parse_stream(args.input)
        analysis = analyze(info, args.frame)
        if args.command == "inspect":
            if args.json:
                print(json.dumps(_inspect_payload(info, analysis), ensure_ascii=False, indent=2))
            else:
                _print_inspect(info, analysis)
            return 0

        available = _parse_available(args.available, info)
        verification = None
        if not args.no_decode and analysis.local_allowed:
            verification = verify_tiles(
                info,
                analysis.frame_slices,
                available,
                frame_index=args.frame,
                ffmpeg=args.ffmpeg,
            )
        output = args.output or Path("out") / args.input.stem
        report_path = generate_report(
            info, analysis, available, output, verification
        )
        print(f"报告: {report_path}")
        if not analysis.local_allowed:
            print("局部展示: 拒绝（独立解码条件不满足）")
            for reason in analysis.blocked_reasons:
                print(f"  - {reason}")
        elif verification is None:
            print("局部展示: 未核验")
        elif verification.error:
            print(f"局部展示: 失败（{verification.error}）")
            return 1
        elif verification.exact:
            all_ids = {tile.id for tile in info.layout.tiles}
            if available == all_ids:
                print("局部展示: 全部瓦片逐像素一致")
            else:
                print("局部展示: 可用瓦片逐像素一致，缺失区域已掩码")
        else:
            print("局部展示: 核验不一致，报告标记为失败")
            return 1
        return 0
    except (OSError, ValueError, IndexError, DecodeError) as error:
        print(f"错误: {error}", file=sys.stderr)
        return 2


def _parse_available(value: str, info) -> set[int]:
    tile_ids = {tile.id for tile in info.layout.tiles}
    if value.strip().lower() == "all":
        return tile_ids
    if not value.strip():
        raise ValueError("--available 不能为空")
    try:
        result = {int(item.strip()) for item in value.split(",") if item.strip()}
    except ValueError as error:
        raise ValueError("--available 需为逗号分隔的整数，例如 0,2") from error
    unknown = sorted(result - tile_ids)
    if unknown:
        raise ValueError(f"未知瓦片编号 {unknown}，有效编号为 {sorted(tile_ids)}")
    if not result:
        raise ValueError("至少选择一个可用瓦片")
    return result


def _inspect_payload(info, analysis) -> dict:
    frame_counts = slice_tile_ranges(info.layout, analysis.frame_slices)
    return {
        **info.to_basic_dict(),
        "frame": analysis.frame_index,
        "local_decode_allowed": analysis.local_allowed,
        "blocked_reasons": analysis.blocked_reasons,
        "tiles": [
            {
                "id": tile.id,
                "x": tile.x,
                "y": tile.y,
                "width": tile.width,
                "height": tile.height,
                "entry_offsets": metric.entry_offsets,
                "absolute_offsets": metric.absolute_offsets,
                "substream_bytes": metric.substream_bytes,
                "slice_count_frame": frame_counts.get(tile.id, 0),
                "slice_count_all_frames": metric.slice_count,
                "average_bytes": metric.average_bytes,
                "average_bits": metric.average_bits,
            }
            for tile, metric in zip(info.layout.tiles, analysis.metrics)
        ],
        "dependencies": [
            {
                "source": edge.source,
                "target": edge.target,
                "kind": edge.kind,
                "detail": edge.detail,
            }
            for edge in analysis.dependencies
        ],
    }


def _print_inspect(info, analysis) -> None:
    sps = info.sps
    assert sps is not None and info.layout is not None
    print(f"文件: {info.path}")
    print(
        f"画面: {sps.width}x{sps.height}  CTB: {sps.ctb_size}  "
        f"瓦片: {info.layout.num_columns}x{info.layout.num_rows}"
    )
    print(
        f"切片: {len(analysis.frame_slices)}（第 {analysis.frame_index} 帧）  "
        f"独立局部解码: {'允许' if analysis.local_allowed else '拒绝'}"
    )
    counts = slice_tile_ranges(info.layout, analysis.frame_slices)
    header = (
        f"{'ID':>3} {'区域':>15} {'入口点偏移':>18} {'子流(B)':>12} "
        f"{'切片':>6} {'平均码量':>12}  依赖"
    )
    print(header)
    print("-" * len(header))
    for tile, metric in zip(info.layout.tiles, analysis.metrics):
        offsets = ",".join(str(value) for value in metric.entry_offsets) or "0"
        sizes = ",".join(str(value) for value in metric.substream_bytes) or "-"
        deps = [
            f"T{edge.source}"
            for edge in analysis.cross_tile_dependencies
            if edge.target == tile.id
        ]
        print(
            f"{tile.id:>3} {f'{tile.x},{tile.y} {tile.width}x{tile.height}':>15} "
            f"{offsets:>18} {sizes:>12} {counts.get(tile.id, 0):>6} "
            f"{metric.average_bytes:>10.0f} B  {','.join(deps) or '无'}"
        )
    if not analysis.local_allowed:
        print("\n未执行局部展示的原因:")
        for reason in analysis.blocked_reasons:
            print(f"  - {reason}")
    elif analysis.cross_tile_dependencies:
        print("\n跨瓦片依赖:")
        for edge in analysis.cross_tile_dependencies:
            print(f"  - T{edge.source} -> T{edge.target} [{edge.kind}] {edge.detail}")
    else:
        print("\n跨瓦片依赖: 无（每个瓦片仅依赖自身内部顺序）")
