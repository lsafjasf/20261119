"""Container, frame-header, CRC, and resynchronization logic."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .bitreader import BitReader, BitReaderError
from .crc import crc8, crc16
from .model import (
    Frame,
    Inspection,
    MetadataBlock,
    Recovery,
    StreamInfo,
    SyncRejection,
)
from .subframe import SubframeError, decode_channels


class FlacFormatError(ValueError):
    """Raised when the FLAC container or a required field is unusable."""


_BLOCK_SIZE_CODES = {
    1: 192,
    2: 576,
    3: 1152,
    4: 2304,
    5: 4608,
    8: 256,
    9: 512,
    10: 1024,
    11: 2048,
    12: 4096,
    13: 8192,
    14: 16384,
    15: 32768,
}

_SAMPLE_RATE_CODES = {
    1: 88200,
    2: 176400,
    3: 192000,
    4: 8000,
    5: 16000,
    6: 22050,
    7: 24000,
    8: 32000,
    9: 44100,
    10: 48000,
    11: 96000,
}

_SAMPLE_SIZE_CODES = {1: 8, 2: 12, 4: 16, 5: 20, 6: 24, 7: 32}

_METADATA_NAMES = {
    0: "STREAMINFO",
    1: "PADDING",
    2: "APPLICATION",
    3: "SEEKTABLE",
    4: "VORBIS_COMMENT",
    5: "CUESHEET",
    6: "PICTURE",
}

_CHANNEL_ASSIGNMENTS = {
    0: "independent-1",
    1: "independent-2",
    2: "independent-3",
    3: "independent-4",
    4: "independent-5",
    5: "independent-6",
    6: "independent-7",
    7: "independent-8",
    8: "left-side",
    9: "right-side",
    10: "mid-side",
}


def _read_utf8_uint(reader: BitReader) -> int:
    first = reader.read(8)
    if first & 0x80 == 0:
        return first

    leading = 0
    mask = 0x80
    while first & mask:
        leading += 1
        mask >>= 1
    if leading < 2 or leading > 7:
        raise FlacFormatError(f"invalid UTF-8 frame number prefix 0x{first:02x}")

    value = first & ((1 << (7 - leading)) - 1)
    for _ in range(leading - 1):
        continuation = reader.read(8)
        if continuation & 0xC0 != 0x80:
            raise FlacFormatError("invalid UTF-8 frame number continuation")
        value = (value << 6) | (continuation & 0x3F)
    return value


def _parse_stream_info(data: bytes) -> StreamInfo:
    if len(data) != 34:
        raise FlacFormatError(f"STREAMINFO length is {len(data)}, expected 34")
    reader = BitReader(data)
    min_block_size = reader.read(16)
    max_block_size = reader.read(16)
    min_frame_size = reader.read(24)
    max_frame_size = reader.read(24)
    sample_rate = reader.read(20)
    channels = reader.read(3) + 1
    bits_per_sample = reader.read(5) + 1
    total_samples = reader.read(36)
    md5 = data[18:34]
    return StreamInfo(
        min_block_size=min_block_size,
        max_block_size=max_block_size,
        min_frame_size=min_frame_size,
        max_frame_size=max_frame_size,
        sample_rate=sample_rate,
        channels=channels,
        bits_per_sample=bits_per_sample,
        total_samples=total_samples,
        md5=md5,
    )


def _parse_metadata(
    data: bytes,
) -> tuple[StreamInfo, list[MetadataBlock], int]:
    if len(data) < 4 or data[:4] != b"fLaC":
        raise FlacFormatError("missing native FLAC marker 'fLaC'")

    blocks: list[MetadataBlock] = []
    stream_info = None
    offset = 4
    while True:
        if offset + 4 > len(data):
            raise FlacFormatError("truncated metadata block header")
        header = data[offset]
        is_last = bool(header & 0x80)
        block_type = header & 0x7F
        length = int.from_bytes(data[offset + 1 : offset + 4], "big")
        body_start = offset + 4
        body_end = body_start + length
        if body_end > len(data):
            raise FlacFormatError(
                f"metadata block at 0x{offset:x} extends beyond end of file"
            )
        if block_type == 0x7F:
            raise FlacFormatError("metadata block type 127 is invalid")

        name = _METADATA_NAMES.get(block_type, f"RESERVED-{block_type}")
        blocks.append(
            MetadataBlock(
                offset=offset,
                block_type=block_type,
                length=length,
                is_last=is_last,
                name=name,
            )
        )
        if block_type == 0:
            if stream_info is not None:
                raise FlacFormatError("duplicate STREAMINFO block")
            stream_info = _parse_stream_info(data[body_start:body_end])

        offset = body_end
        if is_last:
            break

    if stream_info is None:
        raise FlacFormatError("STREAMINFO block is missing")
    if not blocks or blocks[0].block_type != 0:
        raise FlacFormatError("STREAMINFO must be the first metadata block")
    if stream_info.sample_rate == 0:
        raise FlacFormatError("STREAMINFO sample rate is zero")
    return stream_info, blocks, offset


def _read_header(frame: Frame, data: bytes, stream_info: StreamInfo) -> None:
    reader = BitReader(data, frame.offset * 8)
    sync = reader.read(14)
    reserved = reader.read(1)
    blocking = reader.read(1)
    block_size_code = reader.read(4)
    sample_rate_code = reader.read(4)
    assignment = reader.read(4)
    sample_size_code = reader.read(3)
    reserved2 = reader.read(1)

    frame.blocking_strategy = "fixed" if blocking == 0 else "variable"
    frame.channel_assignment_code = assignment
    frame.channel_assignment = _CHANNEL_ASSIGNMENTS.get(
        assignment, f"reserved-{assignment}"
    )
    if sync != 0x3FFE:
        frame.errors.append(f"bad frame sync 0x{sync:04x}")
    if reserved:
        frame.errors.append("frame header reserved bit is set")
    if reserved2:
        frame.errors.append("frame header reserved2 bit is set")

    number = _read_utf8_uint(reader)
    frame.header_number = number

    if block_size_code == 6:
        block_size = reader.read(8) + 1
    elif block_size_code == 7:
        block_size = reader.read(16) + 1
    else:
        block_size = _BLOCK_SIZE_CODES.get(block_size_code)
    frame.block_size = block_size
    if block_size_code == 0:
        frame.errors.append("reserved block size code 0")
    if block_size is not None and not 1 <= block_size <= 65535:
        frame.errors.append(f"invalid block size {block_size}")

    if sample_rate_code == 0:
        sample_rate = stream_info.sample_rate
    elif sample_rate_code == 12:
        sample_rate = reader.read(8) * 1000
    elif sample_rate_code == 13:
        sample_rate = reader.read(16)
    elif sample_rate_code == 14:
        sample_rate = reader.read(16) * 10
    elif sample_rate_code == 15:
        sample_rate = None
        frame.errors.append("reserved sample rate code 15")
    else:
        sample_rate = _SAMPLE_RATE_CODES[sample_rate_code]
    frame.sample_rate = sample_rate
    if sample_rate is not None and sample_rate != stream_info.sample_rate:
        frame.errors.append(
            f"frame sample rate {sample_rate} differs from STREAMINFO "
            f"{stream_info.sample_rate}"
        )

    if sample_size_code == 0:
        bits_per_sample = stream_info.bits_per_sample
    else:
        bits_per_sample = _SAMPLE_SIZE_CODES.get(sample_size_code)
    frame.bits_per_sample = bits_per_sample
    if sample_size_code == 3:
        frame.errors.append("reserved sample size code 3")
    if bits_per_sample is not None and bits_per_sample != stream_info.bits_per_sample:
        frame.errors.append(
            f"frame sample size {bits_per_sample} differs from STREAMINFO "
            f"{stream_info.bits_per_sample}"
        )

    if assignment <= 7:
        channels = assignment + 1
    elif assignment <= 10:
        channels = 2
    else:
        channels = None
        frame.errors.append(f"reserved channel assignment {assignment}")
    frame.channels = channels
    if channels is not None and channels != stream_info.channels:
        frame.errors.append(
            f"frame channel count {channels} differs from STREAMINFO "
            f"{stream_info.channels}"
        )

    reader.align_to_byte()
    crc_offset = reader.byte_position
    frame.header_crc_expected = reader.read(8)
    header_bytes = data[frame.offset : crc_offset]
    frame.header_crc_calculated = crc8(header_bytes)
    frame.header_crc_ok = frame.header_crc_expected == frame.header_crc_calculated
    frame.header_size = reader.byte_position - frame.offset
    if not frame.header_crc_ok:
        frame.errors.append(
            f"header CRC8 mismatch: stored 0x{frame.header_crc_expected:02x}, "
            f"computed 0x{frame.header_crc_calculated:02x}"
        )

    if frame.blocking_strategy == "fixed":
        frame.sample_number = number * stream_info.max_block_size
    else:
        frame.sample_number = number


def _parse_frame(data: bytes, offset: int, stream_info: StreamInfo) -> Frame:
    frame = Frame(offset=offset)
    try:
        _read_header(frame, data, stream_info)
    except (BitReaderError, FlacFormatError) as error:
        frame.errors.append(f"header: {error}")
        return frame

    if (
        frame.block_size is None
        or frame.channels is None
        or frame.bits_per_sample is None
        or frame.channel_assignment_code is None
    ):
        frame.errors.append("header does not determine the subframe layout")
        return frame

    body_offset = offset + (frame.header_size or 0)
    reader = BitReader(data, body_offset * 8)
    try:
        subframes, decoded = decode_channels(
            reader,
            frame.block_size,
            frame.bits_per_sample,
            frame.channel_assignment_code,
        )
        frame.subframes = subframes
        frame.decoded_channels = decoded
    except (SubframeError, BitReaderError, IndexError) as error:
        frame.errors.append(f"subframe: {error}")
        return frame

    padding = reader.align_to_byte()
    if padding:
        frame.errors.append(f"frame padding bits are non-zero (0x{padding:x})")
    crc_offset = reader.byte_position
    if crc_offset + 2 > len(data):
        frame.errors.append("frame CRC16 is truncated")
        return frame

    frame.frame_crc_expected = int.from_bytes(data[crc_offset : crc_offset + 2], "big")
    frame.frame_crc_calculated = crc16(data[offset:crc_offset])
    frame.frame_crc_ok = frame.frame_crc_expected == frame.frame_crc_calculated
    frame.end = crc_offset + 2
    if not frame.frame_crc_ok:
        frame.errors.append(
            f"frame CRC16 mismatch: stored 0x{frame.frame_crc_expected:04x}, "
            f"computed 0x{frame.frame_crc_calculated:04x}"
        )
    return frame


def _is_sync(data: bytes, offset: int) -> bool:
    return (
        offset + 1 < len(data)
        and data[offset] == 0xFF
        and data[offset + 1] & 0xFE == 0xF8
    )


def _set_sequence(frame: Frame, previous: Frame | None) -> None:
    if frame.header_number is None:
        frame.sequence_status = "UNKNOWN (missing frame/sample number)"
        return
    if frame.blocking_strategy == "fixed":
        actual = frame.header_number
        expected = previous.header_number + 1 if previous else 0
        label = "frame"
    else:
        actual = frame.sample_number
        expected = (
            previous.sample_number + previous.block_size
            if previous and previous.sample_number is not None
            else 0
        )
        label = "sample"

    if actual == expected and previous is None and expected == 0:
        frame.sequence_status = "START"
    elif actual == expected:
        frame.sequence_status = "OK"
    elif actual > expected:
        frame.sequence_status = f"GAP (expected {label} {expected}, got {actual})"
    else:
        frame.sequence_status = f"BACKWARD (expected {label} {expected}, got {actual})"


def _sequence_compatible(frame: Frame, previous: Frame | None) -> bool:
    if previous is None:
        return True
    if frame.blocking_strategy == "fixed" and frame.header_number is not None:
        return frame.header_number > (previous.header_number or -1)
    if frame.sample_number is not None and previous.sample_number is not None:
        return frame.sample_number > previous.sample_number
    return False


def _candidate_reason(frame: Frame) -> str:
    checks = []
    if frame.header_crc_ok is False:
        checks.append("header CRC8 failed")
    elif frame.header_crc_ok is None:
        checks.append("header unparsable")
    if frame.frame_crc_ok is False:
        checks.append("frame CRC16 failed")
    elif frame.frame_crc_ok is None:
        checks.append("frame end unparsable")
    if not checks:
        checks.extend(frame.errors[:2])
    return "; ".join(checks) if checks else "structural validation failed"


def _find_next_frame(
    data: bytes, start: int, stream_info: StreamInfo, previous: Frame | None
) -> tuple[Frame | None, list[SyncRejection]]:
    cursor = max(0, start)
    rejected: list[SyncRejection] = []
    while cursor + 1 < len(data):
        offset = data.find(b"\xff", cursor)
        if offset < 0 or offset + 1 >= len(data):
            break
        if not _is_sync(data, offset):
            cursor = offset + 1
            continue

        candidate = _parse_frame(data, offset, stream_info)
        if (
            candidate.structurally_valid
            and _sequence_compatible(candidate, previous)
        ):
            return candidate, rejected
        if candidate.structurally_valid:
            reason = "frame/sample number does not follow the last valid frame"
        else:
            reason = _candidate_reason(candidate)
        rejected.append(SyncRejection(offset=offset, reason=reason))
        cursor = offset + 1
    return None, rejected


def _scan_audio(
    data: bytes, audio_offset: int, stream_info: StreamInfo
) -> tuple[list[Frame], list[Recovery], list[str]]:
    frames: list[Frame] = []
    recoveries: list[Recovery] = []
    errors: list[str] = []
    offset = audio_offset
    previous_valid: Frame | None = None

    while offset < len(data):
        if not _is_sync(data, offset):
            recovered, rejected = _find_next_frame(
                data, offset, stream_info, previous_valid
            )
            if recovered is None:
                errors.append(f"no valid frame found from offset 0x{offset:x}")
                break
            recoveries.append(
                Recovery(
                    damaged_offset=offset,
                    resume_offset=recovered.offset,
                    rejected=rejected,
                )
            )
            _set_sequence(recovered, previous_valid)
            frames.append(recovered)
            previous_valid = recovered
            offset = recovered.end or len(data)
            continue

        frame = _parse_frame(data, offset, stream_info)
        frames.append(frame)
        if frame.structurally_valid:
            _set_sequence(frame, previous_valid)
            previous_valid = frame
            offset = frame.end or len(data)
            continue

        # A CRC-failed frame's computed end is not trusted as an audio boundary.
        # Search forward and accept only a fully validated candidate frame.
        recovered, rejected = _find_next_frame(
            data, offset + 1, stream_info, previous_valid
        )
        if recovered is None:
            errors.append(f"no valid frame found after damaged frame at 0x{offset:x}")
            break
        recoveries.append(
            Recovery(
                damaged_offset=offset,
                resume_offset=recovered.offset,
                rejected=rejected,
            )
        )
        _set_sequence(recovered, previous_valid)
        frames.append(recovered)
        previous_valid = recovered
        offset = recovered.end or len(data)

    return frames, recoveries, errors


def _decoded_pcm_md5(
    frames: list[Frame], stream_info: StreamInfo
) -> tuple[str | None, bool]:
    digest = hashlib.md5()
    sample_count = 0
    for frame in frames:
        if not frame.structurally_valid or frame.decoded_channels is None:
            return None, False
        if frame.sequence_status not in {"START", "OK"}:
            return None, False
        if frame.block_size is None or frame.bits_per_sample is None:
            return None, False
        width = (frame.bits_per_sample + 7) // 8
        for index in range(frame.block_size):
            for channel in frame.decoded_channels:
                digest.update(int(channel[index]).to_bytes(width, "little", signed=True))
        sample_count += frame.block_size

    complete = bool(frames)
    if stream_info.total_samples and sample_count != stream_info.total_samples:
        complete = False
    return digest.hexdigest() if complete else None, complete


def inspect_bytes(data: bytes, path: str | None = None) -> Inspection:
    stream_info, metadata_blocks, audio_offset = _parse_metadata(data)
    if audio_offset > len(data):
        raise FlacFormatError("audio offset is beyond end of file")
    frames, recoveries, errors = _scan_audio(data, audio_offset, stream_info)
    decoded_md5, md5_complete = _decoded_pcm_md5(frames, stream_info)
    return Inspection(
        path=path,
        data_size=len(data),
        stream_info=stream_info,
        metadata_blocks=metadata_blocks,
        audio_offset=audio_offset,
        frames=frames,
        recoveries=recoveries,
        decoded_md5=decoded_md5,
        md5_complete=md5_complete,
        errors=errors,
    )


def inspect_file(path: str | Path) -> Inspection:
    file_path = Path(path)
    return inspect_bytes(file_path.read_bytes(), str(file_path))
