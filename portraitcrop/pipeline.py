"""End-to-end solve, diagnose, export, and report orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .diagnostics import diagnose, summarize_conflicts
from .io import write_conflicts, write_json, write_windows_csv, write_windows_json
from .media import export_crop_video
from .metrics import compute_metrics
from .models import AnnotationSet, CropSpec, Pin
from .report import render_markdown, write_markdown
from .solver import SolverConfig, solve_path


def run_pipeline(
    video_path: str | Path,
    annotations: AnnotationSet,
    crop: CropSpec,
    config: SolverConfig,
    pins: list[Pin],
    output_dir: str | Path,
    title: str,
    ffmpeg: str | None = None,
    export_video: bool = True,
    keep_audio: bool = True,
    crf: int = 20,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    result = solve_path(annotations, crop, config, pins)
    conflicts = diagnose(
        annotations, crop, result, min_confidence=config.min_confidence
    )
    conflict_summary = summarize_conflicts(conflicts)
    metrics = compute_metrics(annotations, result, config)

    output_files: dict[str, str] = {}
    if result.status == "ok":
        windows_json = output / "windows.json"
        windows_csv = output / "windows.csv"
        write_windows_json(
            windows_json,
            result.windows,
            source_video=str(video_path),
            source_width=annotations.width,
            source_height=annotations.height,
            fps=annotations.fps,
            crop={
                "x": crop.x_min,
                "y": crop.y,
                "w": crop.width,
                "h": crop.height,
            },
        )
        write_windows_csv(windows_csv, result.windows)
        output_files["逐帧窗口坐标 JSON"] = str(windows_json)
        output_files["逐帧窗口坐标 CSV"] = str(windows_csv)

    conflicts_path = output / "conflicts.json"
    write_conflicts(
        conflicts_path,
        conflicts,
        crop_width=crop.width,
        crop_height=crop.height,
        summary=conflict_summary,
    )
    output_files["冲突清单"] = str(conflicts_path)

    metrics_path = output / "metrics.json"
    write_json(metrics_path, metrics)
    output_files["量化指标"] = str(metrics_path)

    export_meta: dict[str, Any] | None = None
    if result.status == "ok" and export_video:
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required for video export")
        portrait_path = output / "portrait.mp4"
        export_meta = export_crop_video(
            video_path,
            portrait_path,
            annotations.width,
            annotations.height,
            crop,
            result.windows,
            annotations.fps,
            ffmpeg,
            keep_audio=keep_audio,
            crf=crf,
        )
        write_json(output / "export.json", export_meta)
        output_files["实际裁切视频"] = str(portrait_path)

    report_path = output / "report.md"
    output_files["说明报告"] = str(report_path)
    report = render_markdown(
        title,
        annotations,
        result,
        metrics,
        conflict_summary,
        output_files,
    )
    write_markdown(report_path, report)

    return {
        "status": result.status,
        "title": title,
        "output_dir": str(output),
        "failed_frame": result.failed_frame,
        "reason": result.reason,
        "warnings": result.warnings,
        "metrics": metrics,
        "conflict_count": len(conflicts),
        "conflict_groups": conflict_summary,
        "files": output_files,
        "export": export_meta,
    }
