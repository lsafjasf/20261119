"""Text and JSON rendering for inspection results."""

from __future__ import annotations

from typing import Any

from .model import Frame, Inspection


def _crc_value(value: int | None, width: int) -> str:
    return "n/a" if value is None else f"0x{value:0{width}x}"


def _crc_status(value: bool | None) -> str:
    if value is None:
        return "NOT EVALUATED"
    return "PASS" if value else "FAIL"


def _render_frame(lines: list[str], frame: Frame, index: int) -> None:
    size = "unknown" if frame.size is None else str(frame.size)
    lines.append(f"Frame {index} @ 0x{frame.offset:x} size={size}")
    lines.append(
        "  "
        f"blocking={frame.blocking_strategy or 'unknown'} "
        f"header_number={frame.header_number} "
        f"sample_number={frame.sample_number} "
        f"block_size={frame.block_size}"
    )
    assignment = frame.channel_assignment or "unknown"
    lines.append(
        "  "
        f"channel_assignment={assignment} (code {frame.channel_assignment_code}) "
        f"channels={frame.channels} sample_rate={frame.sample_rate} "
        f"bits_per_sample={frame.bits_per_sample}"
    )
    lines.append(
        "  "
        f"header CRC8: stored={_crc_value(frame.header_crc_expected, 2)} "
        f"computed={_crc_value(frame.header_crc_calculated, 2)} "
        f"{_crc_status(frame.header_crc_ok)}"
    )
    lines.append(
        "  "
        f"frame CRC16: stored={_crc_value(frame.frame_crc_expected, 4)} "
        f"computed={_crc_value(frame.frame_crc_calculated, 4)} "
        f"{_crc_status(frame.frame_crc_ok)}"
    )
    state = "valid audio" if frame.valid_audio else "NOT valid audio"
    lines.append(
        f"  sequence={frame.sequence_status}; structure={state}"
    )
    if frame.subframes:
        for subframe in frame.subframes:
            lines.append(
                f"  ch{subframe.channel}: encoding={subframe.kind} "
                f"block_size={len(subframe.samples)} "
                f"bits={subframe.declared_bits}->{subframe.effective_bits} "
                f"wasted={subframe.wasted_bits} | {subframe.detail}"
            )
    else:
        lines.append("  subframes: unavailable")
    for error in frame.errors:
        lines.append(f"  error: {error}")


def render_text(result: Inspection) -> str:
    lines = [f"FLAC: {result.path or '<memory>'} ({result.data_size} bytes)"]
    stream = result.stream_info
    if stream is None:
        lines.append("STREAMINFO: unavailable")
    else:
        lines.extend(
            [
                "Metadata blocks:",
                *[
                    f"  @0x{block.offset:x} {block.name} length={block.length}"
                    + (" (last)" if block.is_last else "")
                    for block in result.metadata_blocks
                ],
                "STREAMINFO:",
                f"  block_size={stream.min_block_size}..{stream.max_block_size}",
                f"  frame_size={stream.min_frame_size}..{stream.max_frame_size}",
                f"  sample_rate={stream.sample_rate} channels={stream.channels} "
                f"bits_per_sample={stream.bits_per_sample} "
                f"total_samples={stream.total_samples}",
                f"  complete PCM MD5: {stream.md5_hex}",
            ]
        )

    if result.audio_offset is None:
        lines.append("Audio frames start: unknown")
    else:
        lines.append(f"Audio frames start: 0x{result.audio_offset:x}")
    lines.append(f"Frames ({len(result.frames)}):")
    for index, frame in enumerate(result.frames):
        _render_frame(lines, frame, index)

    if result.recoveries:
        lines.append("Recovery scans:")
        for index, recovery in enumerate(result.recoveries, 1):
            lines.append(
                f"  {index}: damaged @0x{recovery.damaged_offset:x}, "
                f"resumed @0x{recovery.resume_offset:x}, "
                f"rejected_sync_candidates={len(recovery.rejected)}"
            )
            for rejected in recovery.rejected:
                lines.append(
                    f"    rejected sync @0x{rejected.offset:x}: {rejected.reason}"
                )

    lines.append("PCM integrity:")
    if stream is None:
        lines.append("  STREAMINFO MD5: unavailable")
    else:
        lines.append(f"  STREAMINFO MD5: {stream.md5_hex}")
    lines.append(
        "  decoded MD5: "
        + (
            result.decoded_md5
            if result.decoded_md5 is not None
            else "unavailable (missing or invalid/gapped audio frames)"
        )
    )
    if result.md5_matches is None:
        lines.append("  result: NOT VERIFIED")
    else:
        lines.append(f"  result: {'MATCH' if result.md5_matches else 'MISMATCH'}")

    valid = sum(frame.structurally_valid for frame in result.frames)
    valid_audio = sum(frame.valid_audio for frame in result.frames)
    lines.append(
        f"Summary: frames={len(result.frames)} structural_valid={valid} "
        f"valid_audio={valid_audio} invalid={len(result.invalid_frames)} "
        f"recoveries={len(result.recoveries)}"
    )
    if result.errors:
        for error in result.errors:
            lines.append(f"Error: {error}")
    status = (
        "OK"
        if not result.errors
        and not result.invalid_frames
        and not result.recoveries
        and result.md5_matches is not False
        else "CORRUPT"
    )
    lines.append(f"Status: {status}")
    return "\n".join(lines)


def _frame_dict(frame: Frame) -> dict[str, Any]:
    return {
        "offset": frame.offset,
        "end": frame.end,
        "size": frame.size,
        "blocking_strategy": frame.blocking_strategy,
        "header_number": frame.header_number,
        "sample_number": frame.sample_number,
        "block_size": frame.block_size,
        "sample_rate": frame.sample_rate,
        "channel_assignment_code": frame.channel_assignment_code,
        "channel_assignment": frame.channel_assignment,
        "channels": frame.channels,
        "bits_per_sample": frame.bits_per_sample,
        "header_crc": {
            "stored": frame.header_crc_expected,
            "computed": frame.header_crc_calculated,
            "ok": frame.header_crc_ok,
        },
        "frame_crc": {
            "stored": frame.frame_crc_expected,
            "computed": frame.frame_crc_calculated,
            "ok": frame.frame_crc_ok,
        },
        "sequence_status": frame.sequence_status,
        "structurally_valid": frame.structurally_valid,
        "valid_audio": frame.valid_audio,
        "errors": frame.errors,
        "subframes": [
            {
                "channel": subframe.channel,
                "encoding": subframe.kind,
                "detail": subframe.detail,
                "block_size": len(subframe.samples),
                "declared_bits": subframe.declared_bits,
                "effective_bits": subframe.effective_bits,
                "wasted_bits": subframe.wasted_bits,
            }
            for subframe in frame.subframes
        ],
    }


def as_dict(result: Inspection) -> dict[str, Any]:
    stream = result.stream_info
    return {
        "file": result.path,
        "size": result.data_size,
        "stream_info": (
            None
            if stream is None
            else {
                "min_block_size": stream.min_block_size,
                "max_block_size": stream.max_block_size,
                "min_frame_size": stream.min_frame_size,
                "max_frame_size": stream.max_frame_size,
                "sample_rate": stream.sample_rate,
                "channels": stream.channels,
                "bits_per_sample": stream.bits_per_sample,
                "total_samples": stream.total_samples,
                "pcm_md5": stream.md5_hex,
            }
        ),
        "metadata_blocks": [
            {
                "offset": block.offset,
                "type": block.block_type,
                "name": block.name,
                "length": block.length,
                "last": block.is_last,
            }
            for block in result.metadata_blocks
        ],
        "audio_offset": result.audio_offset,
        "frames": [_frame_dict(frame) for frame in result.frames],
        "pcm_md5": {
            "streaminfo": stream.md5_hex if stream else None,
            "decoded": result.decoded_md5,
            "complete": result.md5_complete,
            "match": result.md5_matches,
        },
        "recoveries": [
            {
                "damaged_offset": recovery.damaged_offset,
                "resume_offset": recovery.resume_offset,
                "rejected": [
                    {"offset": rejected.offset, "reason": rejected.reason}
                    for rejected in recovery.rejected
                ],
            }
            for recovery in result.recoveries
        ],
        "summary": {
            "frames": len(result.frames),
            "structural_valid": sum(frame.structurally_valid for frame in result.frames),
            "valid_audio": sum(frame.valid_audio for frame in result.frames),
            "invalid": len(result.invalid_frames),
            "recoveries": len(result.recoveries),
        },
        "errors": result.errors,
    }
