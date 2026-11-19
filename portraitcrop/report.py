"""Human-readable Markdown reports for crop runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import AnnotationSet, SolveResult


def _percent(value: float | None) -> str:
    return f"{100.0 * float(value or 0.0):.2f}%"


def _number(value: Any, digits: int = 2) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(
    title: str,
    annotations: AnnotationSet,
    result: SolveResult,
    metrics: dict[str, Any],
    conflicts: list[dict[str, Any]],
    output_files: dict[str, str],
) -> str:
    lines = [f"# {title}", ""]
    if result.status != "ok":
        lines.extend(
            [
                "## 求解状态",
                "",
                f"- 状态：**{result.status}**",
                f"- 失败帧：`{result.failed_frame}`",
                f"- 原因：{result.reason}",
                "",
            ]
        )
    else:
        constraint = metrics["constraints"]
        movement = metrics["motion"]
        coverage = metrics["coverage"]
        lines.extend(
            [
                "## 求解状态",
                "",
                f"- 状态：**ok**，共 `{metrics['frame_count']}` 帧，"
                f"`{metrics['duration_s']:.3f}` 秒",
                f"- 裁切窗口：`{result.crop.width}x{result.crop.height}`，"
                f"横向范围 `[{metrics['crop']['x_range'][0]}, "
                f"{metrics['crop']['x_range'][1]}]`",
                f"- 置信度加权主体覆盖率：均值 `{_percent(coverage['mean_weighted'])}`，"
                f"最低 `{_percent(coverage['min_weighted'])}`",
                f"- 全主体完整入镜帧占比：`{_percent(coverage['full_frame_rate'])}`",
                f"- 最大平移速度：`{movement['max_speed_px_s']:.2f} px/s` "
                f"(上限 `{constraint['max_speed_limit_px_s']:.2f}`)",
                f"- 最大加速度：`{movement['max_acceleration_px_s2']:.2f} px/s²` "
                f"(上限 `{constraint['max_acceleration_limit_px_s2']:.2f}`)",
                f"- 路径抖动 RMS：`{movement['jitter_rms_px_s2']:.2f} px/s²`；"
                f"方向反转 `{movement['direction_reversals']}` 次",
                f"- 越界帧：`{constraint['out_of_bounds_frames']}`；"
                f"速度违规帧：`{constraint['speed_violation_frames']}`；"
                f"加速度违规帧：`{constraint['acceleration_violation_frames']}`",
                "",
                "## 分主体覆盖",
                "",
                "| 区域 | 平均覆盖 | 最低覆盖 | 出现帧数 |",
                "|---|---:|---:|---:|",
            ]
        )
        for region_id, values in coverage["by_subject"].items():
            lines.append(
                f"| `{region_id}` | {_percent(values['mean'])} | "
                f"{_percent(values['min'])} | {values['frames_present']} |"
            )
        if not coverage["by_subject"]:
            lines.append("| （无） | 0.00% | 0.00% | 0 |")
        lines.append("")

    if result.resolved_pins and result.windows:
        lines.extend(
            [
                "## 手动构图钉住",
                "",
                "| 帧 | 区域 | 目标 x | 实际 x | 误差 | 类型 | 说明 |",
                "|---:|---|---:|---:|---:|---|---|",
            ]
        )
        for pin_metric in metrics.get("pins", []):
            frame = pin_metric["frame"]
            actual = result.windows[frame]
            resolved = next(
                (
                    item
                    for item in result.resolved_pins
                    if item.pin.frame == frame
                ),
                None,
            )
            regions = ", ".join(pin_metric["region_ids"]) or "x"
            note = resolved.pin.note if resolved else ""
            pin_type = "hard" if pin_metric["hard"] else "soft"
            lines.append(
                f"| {frame} | `{regions}` | {pin_metric['target_x']:.2f} | "
                f"{actual.x} | {pin_metric['error_px']:+.2f} | {pin_type} | {note} |"
            )
        lines.append("")

    lines.extend(
        [
            "## 区域冲突",
            "",
            "冲突表示一个裁切窗口无法同时完整容纳对应区域；"
            "`required_span` 是区域并集宽度，`overrun` 是超出窗口的像素数。",
            "",
        ]
    )
    if conflicts:
        lines.extend(
            [
                "| 区域 | 类型 | 冲突帧数 | 最大超出 | 最差帧 | 必需宽度 |",
                "|---|---|---:|---:|---:|---:|",
            ]
        )
        for item in conflicts[:12]:
            region_text = ", ".join(f"`{region}`" for region in item["region_ids"])
            lines.append(
                f"| {region_text} | `{item['kind']}` | {item['frame_count']} | "
                f"{item['max_overrun_px']:.2f} px | {item['worst_frame']} | "
                f"{item['worst_required_span']:.2f} px |"
            )
        lines.append("")
        if len(conflicts) > 12:
            lines.append(f"其余 `{len(conflicts) - 12}` 组见 `conflicts.json`。")
            lines.append("")
    else:
        lines.extend(["未发现无法同时容纳的区域。", ""])

    lines.extend(["## 产物", ""])
    for label, path in output_files.items():
        suffix = Path(path).name
        lines.append(f"- {label}：`{suffix}`")
    lines.append("")
    return "\n".join(lines)


def write_markdown(path: str | Path, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
