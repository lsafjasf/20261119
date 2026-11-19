"""Human-readable rendering for MP3 audit results."""

from __future__ import annotations

from .parser import AuditResult, FrameRecord


def _frame_flags(frame: FrameRecord) -> str:
    flags = [diagnostic.code for diagnostic in frame.diagnostics]
    return ",".join(flags) if flags else "-"


def _format_history(frame: FrameRecord) -> str:
    reservoir = frame.reservoir
    if reservoir is None:
        return "-"
    if reservoir.history_frame_start is None:
        return f"{reservoir.main_data_begin}/none"
    frame_range = str(reservoir.history_frame_start)
    if reservoir.history_frame_end != reservoir.history_frame_start:
        frame_range += f"-{reservoir.history_frame_end}"
    return (
        f"{reservoir.main_data_begin}/{frame_range}"
        f"@[{reservoir.source_start},{reservoir.source_end})"
    )


def _frame_table(frames: list[FrameRecord]) -> list[str]:
    lines = [
        "index  offset    kbps  bytes  mdb/history(frames@bytes)                  part23  status  flags"
    ]
    for frame in frames:
        header = frame.header
        part23 = "-"
        if frame.side_info is not None:
            part23 = str(frame.side_info.part2_3_bytes)
        lines.append(
            f"{frame.index:>5}  {header.offset:>9}  "
            f"{header.bitrate // 1000:>4}  {header.frame_length:>5}  "
            f"{_format_history(frame):<40}  {part23:>7}  "
            f"{frame.status:<7}  {_frame_flags(frame)}"
        )
    return lines


def render_text(result: AuditResult, *, show_frames: bool = False) -> str:
    lines = [
        f"source: {result.source}",
        f"file: {result.file_size} bytes, audio [{result.audio_start},{result.audio_end})",
    ]
    for tag in result.tags:
        version = f" {tag.version}" if tag.version else ""
        lines.append(f"tag: {tag.kind}{version} at {tag.offset}, {tag.size} bytes")

    if result.frames:
        first = result.frames[0].header
        lines.append(
            "format: "
            f"{first.version_name} {first.layer_name}, "
            f"{first.sample_rate} Hz, {first.channel_mode_name}"
        )
        lines.append(
            f"frames: {result.frame_count}, bytes: {result.total_frame_bytes}, "
            f"duration: {result.duration:.6f} s"
        )
        lines.append(f"stream: {result.stream_kind}")
        lines.append(
            f"actual bitrate: {result.actual_bitrate / 1000:.3f} kbps"
        )
        histogram = ", ".join(
            f"{bitrate // 1000}k={count}"
            for bitrate, count in sorted(result.bitrate_histogram.items())
        )
        lines.append(f"bitrate histogram: {histogram}")
        damaged = result.damaged_frames
        lines.append(
            f"pre-decode damage: {len(damaged)} frame(s), "
            f"{result.error_count} error diagnostic(s)"
            + (f" {[frame.index for frame in damaged]}" if damaged else "")
        )
        drifted = [frame for frame in result.frames if frame.length_drift is not None]
        if drifted:
            drift_total = sum(frame.length_drift or 0 for frame in drifted)
            lines.append(f"length drift: {len(drifted)} boundary(s), net {drift_total:+d} bytes")
        reservoir_users = [
            frame
            for frame in result.frames
            if frame.side_info is not None and frame.side_info.main_data_begin
        ]
        if reservoir_users:
            maximum = max(frame.side_info.main_data_begin for frame in reservoir_users)
            lines.append(
                f"bit reservoir: {len(reservoir_users)} frame(s) use history, "
                f"max main_data_begin={maximum} bytes"
            )
    else:
        lines.append("frames: 0")

    if result.diagnostics:
        lines.append("diagnostics:")
        for diagnostic in result.diagnostics:
            location = ""
            if diagnostic.frame_index is not None:
                location += f" frame={diagnostic.frame_index}"
            if diagnostic.offset is not None:
                location += f" offset={diagnostic.offset}"
            lines.append(
                f"  [{diagnostic.severity}] {diagnostic.code}{location}: "
                f"{diagnostic.message}"
            )
    else:
        lines.append("diagnostics: none")

    if show_frames:
        lines.append("")
        lines.extend(_frame_table(result.frames))
    elif result.frames:
        lines.append("frame table: use --frames")
    return "\n".join(lines)
