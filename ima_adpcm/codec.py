"""A small, self-describing IMA ADPCM codec.

The encoded stream contains independent blocks.  A block carries every piece
of state needed to decode it (one predictor/index pair per channel), which
makes seeking and error isolation straightforward.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Iterable, Sequence
import zlib


BLOCK_MAGIC = b"IADB"
FILE_MAGIC = b"IADP"
FORMAT_VERSION = 1
FILE_HEADER_SIZE = 20
BLOCK_FIXED_HEADER_SIZE = 12
CHANNEL_STATE_SIZE = 4
CRC_SIZE = 4

# Standard IMA ADPCM tables.  The index is kept in 0..88 inclusive.
STEP_TABLE: tuple[int, ...] = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
    34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130,
    143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449,
    494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411,
    1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026,
    4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
    27086, 29794, 32767,
)
INDEX_TABLE: tuple[int, ...] = (
    -1, -1, -1, -1, 2, 4, 6, 8,
    -1, -1, -1, -1, 2, 4, 6, 8,
)


class CodecError(ValueError):
    """Base class for malformed or unsupported codec data."""


class InvalidBlock(CodecError):
    """A block is truncated, malformed, or fails its integrity check."""


class InvalidContainer(CodecError):
    """The file-level container header or block layout is invalid."""


class CrcMismatch(InvalidBlock):
    """A block's CRC32 does not match its contents."""


@dataclass(frozen=True)
class ChannelState:
    """Decoder state at the start of a block."""

    predictor: int
    step_index: int = 0

    @property
    def step_size(self) -> int:
        return STEP_TABLE[self.step_index]


@dataclass(frozen=True)
class StepTrace:
    """State transition for one encoded nibble.

    ``prediction`` is the predictor *before* applying the quantized
    difference.  ``quantized_difference`` is the signed, dequantized PCM
    difference, and ``reconstructed`` is the predictor after reconstruction.
    """

    frame: int
    channel: int
    code: int
    prediction: int
    quantized_difference: int
    reconstructed: int
    step_index_before: int
    step_index_after: int
    step_size_before: int


@dataclass(frozen=True)
class BlockHeader:
    """Parsed block metadata.

    The payload starts at ``offset + header_size`` and contains
    ``payload_size`` bytes followed by a four-byte little-endian CRC32.
    """

    offset: int
    channels: int
    frame_count: int
    payload_size: int
    reserved: int
    states: tuple[ChannelState, ...]
    header_size: int
    total_size: int


@dataclass(frozen=True)
class FileHeader:
    """Parsed ``IADP`` file header and derived random-access geometry."""

    channels: int
    sample_rate: int
    total_frames: int
    frames_per_block: int
    header_size: int
    block_count: int
    full_block_size: int


@dataclass(frozen=True)
class DecodedBlock:
    """Decoded PCM and optional per-nibble trace for one block."""

    channels: int
    frame_count: int
    samples: tuple[tuple[int, ...], ...]
    initial_states: tuple[ChannelState, ...]
    trace: tuple[StepTrace, ...] = ()
    payload_size: int = 0

    @property
    def sample_count(self) -> int:
        return self.frame_count * self.channels

    def interleaved(self) -> tuple[int, ...]:
        """Return frame-major, channel-minor signed 16-bit samples."""

        return tuple(
            self.samples[channel][frame]
            for frame in range(self.frame_count)
            for channel in range(self.channels)
        )


@dataclass(frozen=True)
class BlockFailure:
    block_index: int
    frame_start: int
    frame_count: int
    message: str


@dataclass(frozen=True)
class Audio:
    """Channel-major decoded PCM plus any recoverable block failures."""

    sample_rate: int
    channels: int
    frames: tuple[tuple[int, ...], ...]
    failures: tuple[BlockFailure, ...] = ()
    traces: tuple[tuple[StepTrace, ...], ...] = ()

    @property
    def frame_count(self) -> int:
        return len(self.frames[0]) if self.frames else 0

    def interleaved(self) -> tuple[int, ...]:
        return tuple(
            self.frames[channel][frame]
            for frame in range(self.frame_count)
            for channel in range(self.channels)
        )


def _clamp_int16(value: int) -> int:
    if value > 32767:
        return 32767
    if value < -32768:
        return -32768
    return value


def _valid_index(index: int) -> int:
    if not 0 <= index < len(STEP_TABLE):
        raise CodecError(f"step index must be in 0..88, got {index}")
    return index


def _normalize_indices(initial_index: int | Sequence[int], channels: int) -> tuple[int, ...]:
    if isinstance(initial_index, int):
        values = (initial_index,) * channels
    else:
        values = tuple(initial_index)
        if len(values) != channels:
            raise CodecError(
                f"initial_index has {len(values)} entries for {channels} channels"
            )
    return tuple(_valid_index(index) for index in values)


def _payload_size(frame_count: int, channels: int) -> int:
    return ((frame_count - 1) * channels + 1) // 2


def block_size(frame_count: int, channels: int) -> int:
    """Encoded byte length of a complete block, including its CRC."""

    if not 1 <= channels <= 255:
        raise InvalidBlock(f"channels must be in 1..255, got {channels}")
    if not 1 <= frame_count <= 65535:
        raise InvalidBlock(f"frame_count must be in 1..65535, got {frame_count}")
    return (
        BLOCK_FIXED_HEADER_SIZE
        + CHANNEL_STATE_SIZE * channels
        + _payload_size(frame_count, channels)
        + CRC_SIZE
    )


def _encode_code(sample: int, predictor: int, step_index: int) -> tuple[int, int, int, int]:
    """Quantize one PCM sample.

    Returns ``(code, new_predictor, new_index, signed_difference)``.  The
    subtraction of ``delta`` from the residual is deliberate: it mirrors the
    decoder bit-for-bit and prevents encoder/decoder state drift.
    """

    step = STEP_TABLE[step_index]
    residual = sample - predictor
    code = 0
    if residual < 0:
        code = 8
        residual = -residual

    delta = step >> 3
    if residual >= step:
        code |= 4
        residual -= step
        delta += step
    step >>= 1
    if residual >= step:
        code |= 2
        residual -= step
        delta += step
    step >>= 1
    if residual >= step:
        code |= 1
        delta += step

    if code & 8:
        new_predictor = _clamp_int16(predictor - delta)
        signed_difference = -delta
    else:
        new_predictor = _clamp_int16(predictor + delta)
        signed_difference = delta
    new_index = max(0, min(88, step_index + INDEX_TABLE[code]))
    return code, new_predictor, new_index, signed_difference


def _decode_code(code: int, predictor: int, step_index: int) -> tuple[int, int, int]:
    """Reconstruct one sample from a nibble.

    Returns ``(new_predictor, signed_difference, new_index)``.  This is the
    canonical IMA reconstruction rule and does not depend on encoder history.
    """

    step = STEP_TABLE[step_index]
    delta = step >> 3
    if code & 4:
        delta += step
    if code & 2:
        delta += step >> 1
    if code & 1:
        delta += step >> 2

    if code & 8:
        new_predictor = _clamp_int16(predictor - delta)
        signed_difference = -delta
    else:
        new_predictor = _clamp_int16(predictor + delta)
        signed_difference = delta
    new_index = max(0, min(88, step_index + INDEX_TABLE[code]))
    return new_predictor, signed_difference, new_index


def _pack_nibbles(codes: Iterable[int]) -> bytes:
    output = bytearray()
    pending: int | None = None
    for code in codes:
        if not 0 <= code <= 15:
            raise CodecError(f"ADPCM code out of range: {code}")
        if pending is None:
            pending = code
        else:
            output.append(pending | (code << 4))
            pending = None
    if pending is not None:
        output.append(pending)
    return bytes(output)


def _unpack_nibbles(payload: bytes, count: int) -> Iterable[int]:
    for position in range(count):
        value = payload[position >> 1]
        yield (value >> 4) & 0x0F if position & 1 else value & 0x0F


def encode_block(
    frames: Sequence[Sequence[int]],
    *,
    initial_index: int | Sequence[int] = 0,
) -> bytes:
    """Encode channel-major PCM frames into one independent IMA block.

    Args:
        frames: One sequence per channel.  All sequences must have the same
            non-zero length.  Frame zero is stored losslessly in the block
            header; later frames are encoded in frame-major/channel-minor
            order.
        initial_index: Initial step index for every channel, or one value per
            channel.  Encoder-created blocks normally start at index zero.
    """

    channels = len(frames)
    if not 1 <= channels <= 255:
        raise CodecError(f"channels must be in 1..255, got {channels}")
    if not frames or not frames[0]:
        raise CodecError("a block must contain at least one frame")
    frame_count = len(frames[0])
    if frame_count > 65535:
        raise CodecError("frame_count exceeds the 16-bit block header limit")
    if any(len(channel) != frame_count for channel in frames):
        raise CodecError("all channels must contain the same number of frames")
    if _payload_size(frame_count, channels) > 65535:
        raise CodecError("block payload exceeds the 16-bit length field")

    indices = list(_normalize_indices(initial_index, channels))
    predictors = [_clamp_int16(int(channel[0])) for channel in frames]
    states = [
        ChannelState(predictor=predictors[channel], step_index=indices[channel])
        for channel in range(channels)
    ]
    codes: list[int] = []
    for frame in range(1, frame_count):
        for channel in range(channels):
            sample = _clamp_int16(int(frames[channel][frame]))
            code, predictors[channel], indices[channel], _ = _encode_code(
                sample, predictors[channel], indices[channel]
            )
            codes.append(code)

    payload = _pack_nibbles(codes)
    if len(payload) > 65535:
        raise CodecError("block payload exceeds the 16-bit length field")
    header = struct.pack(
        "<4sBBHHH",
        BLOCK_MAGIC,
        FORMAT_VERSION,
        channels,
        frame_count,
        len(payload),
        0,
    )
    state_bytes = b"".join(
        struct.pack("<hBB", state.predictor, state.step_index, 0)
        for state in states
    )
    body = header + state_bytes + payload
    return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


def parse_block_header(data: bytes | bytearray | memoryview, *, offset: int = 0) -> BlockHeader:
    """Parse and validate a block header without decoding its PCM."""

    view = memoryview(data)
    if offset < 0:
        raise InvalidBlock("negative block offset")
    minimum = offset + BLOCK_FIXED_HEADER_SIZE
    if len(view) < minimum:
        raise InvalidBlock("truncated block header")
    magic, version, channels, frame_count, payload_size, reserved = struct.unpack_from(
        "<4sBBHHH", view, offset
    )
    if magic != BLOCK_MAGIC:
        raise InvalidBlock(f"bad block magic at byte {offset}: {magic!r}")
    if version != FORMAT_VERSION:
        raise InvalidBlock(f"unsupported block version: {version}")
    if not 1 <= channels:
        raise InvalidBlock("block has zero channels")
    if not 1 <= frame_count:
        raise InvalidBlock("block has zero frames")
    if reserved != 0:
        raise InvalidBlock("non-zero reserved block header field")
    expected_payload = _payload_size(frame_count, channels)
    if payload_size != expected_payload:
        raise InvalidBlock(
            f"payload size {payload_size} does not match "
            f"{frame_count} frames x {channels} channels ({expected_payload})"
        )

    header_size = BLOCK_FIXED_HEADER_SIZE + CHANNEL_STATE_SIZE * channels
    total_size = header_size + payload_size + CRC_SIZE
    if len(view) < offset + total_size:
        raise InvalidBlock("truncated block payload or CRC")

    states: list[ChannelState] = []
    state_offset = offset + BLOCK_FIXED_HEADER_SIZE
    for channel in range(channels):
        predictor, step_index, state_reserved = struct.unpack_from(
            "<hBB", view, state_offset + channel * CHANNEL_STATE_SIZE
        )
        if step_index > 88:
            raise InvalidBlock(
                f"channel {channel} has invalid step index {step_index}"
            )
        if state_reserved != 0:
            raise InvalidBlock(
                f"channel {channel} has non-zero reserved state byte"
            )
        states.append(ChannelState(predictor=predictor, step_index=step_index))

    return BlockHeader(
        offset=offset,
        channels=channels,
        frame_count=frame_count,
        payload_size=payload_size,
        reserved=reserved,
        states=tuple(states),
        header_size=header_size,
        total_size=total_size,
    )


def decode_block(
    data: bytes | bytearray | memoryview,
    *,
    offset: int = 0,
    verify_crc: bool = True,
    collect_trace: bool = False,
) -> DecodedBlock:
    """Decode one self-contained block at ``offset``.

    No state is retained between calls.  ``collect_trace=True`` records the
    predictor, signed quantized difference, reconstructed value, and step
    index transition for every encoded nibble.
    """

    header = parse_block_header(data, offset=offset)
    view = memoryview(data)
    payload_offset = offset + header.header_size
    crc_offset = payload_offset + header.payload_size
    stored_crc = struct.unpack_from("<I", view, crc_offset)[0]
    if verify_crc:
        actual_crc = zlib.crc32(view[offset:crc_offset]) & 0xFFFFFFFF
        if actual_crc != stored_crc:
            raise CrcMismatch(
                f"CRC mismatch at block byte {offset}: "
                f"stored 0x{stored_crc:08x}, actual 0x{actual_crc:08x}"
            )

    predictors = [state.predictor for state in header.states]
    indices = [state.step_index for state in header.states]
    samples: list[list[int]] = [[predictor] for predictor in predictors]
    trace: list[StepTrace] = []
    payload = view[payload_offset:crc_offset]
    code_count = (header.frame_count - 1) * header.channels
    for position, code in enumerate(_unpack_nibbles(payload, code_count)):
        frame = 1 + position // header.channels
        channel = position % header.channels
        predictor_before = predictors[channel]
        index_before = indices[channel]
        predictor_after, difference, index_after = _decode_code(
            code, predictor_before, index_before
        )
        predictors[channel] = predictor_after
        indices[channel] = index_after
        samples[channel].append(predictor_after)
        if collect_trace:
            trace.append(
                StepTrace(
                    frame=frame,
                    channel=channel,
                    code=code,
                    prediction=predictor_before,
                    quantized_difference=difference,
                    reconstructed=predictor_after,
                    step_index_before=index_before,
                    step_index_after=index_after,
                    step_size_before=STEP_TABLE[index_before],
                )
            )

    return DecodedBlock(
        channels=header.channels,
        frame_count=header.frame_count,
        samples=tuple(tuple(channel) for channel in samples),
        initial_states=header.states,
        trace=tuple(trace),
        payload_size=header.payload_size,
    )


def _normalize_frames(frames: Sequence[Sequence[int]], channels: int | None) -> tuple[tuple[int, ...], ...]:
    if frames:
        channel_count = len(frames)
        if channels is not None and channels != channel_count:
            raise CodecError(
                f"channels={channels} but frames contain {channel_count} channels"
            )
        frame_count = len(frames[0])
        if any(len(channel) != frame_count for channel in frames):
            raise CodecError("all channels must contain the same number of frames")
        return tuple(tuple(int(value) for value in channel) for channel in frames)

    channel_count = 1 if channels is None else channels
    if not 1 <= channel_count <= 255:
        raise CodecError(f"channels must be in 1..255, got {channel_count}")
    return tuple(() for _ in range(channel_count))


def encode_file(
    frames: Sequence[Sequence[int]],
    *,
    sample_rate: int,
    frames_per_block: int = 256,
    channels: int | None = None,
    initial_index: int | Sequence[int] = 0,
) -> bytes:
    """Encode channel-major PCM into the ``IADP`` block container."""

    normalized = _normalize_frames(frames, channels)
    channel_count = len(normalized)
    total_frames = len(normalized[0])
    if not 0 <= sample_rate <= 0xFFFFFFFF:
        raise CodecError("sample_rate must fit in an unsigned 32-bit field")
    if not 1 <= frames_per_block <= 65535:
        raise CodecError("frames_per_block must be in 1..65535")
    if total_frames > 0xFFFFFFFF:
        raise CodecError("total frame count exceeds the container limit")
    if _payload_size(frames_per_block, channel_count) > 65535:
        raise CodecError(
            "frames_per_block/channels produce a payload larger than 65535 bytes"
        )

    block_count = (total_frames + frames_per_block - 1) // frames_per_block
    file_header = struct.pack(
        "<4sBBHIIHH",
        FILE_MAGIC,
        FORMAT_VERSION,
        channel_count,
        FILE_HEADER_SIZE,
        sample_rate,
        total_frames,
        frames_per_block,
        0,
    )
    blocks = bytearray()
    for frame_start in range(0, total_frames, frames_per_block):
        frame_end = min(total_frames, frame_start + frames_per_block)
        block_frames = [
            channel[frame_start:frame_end] for channel in normalized
        ]
        blocks.extend(encode_block(block_frames, initial_index=initial_index))
    return file_header + bytes(blocks)


def parse_file_header(data: bytes | bytearray | memoryview) -> FileHeader:
    """Parse the container header and compute its deterministic block layout."""

    view = memoryview(data)
    if len(view) < FILE_HEADER_SIZE:
        raise InvalidContainer("truncated IADP file header")
    (
        magic,
        version,
        channels,
        header_size,
        sample_rate,
        total_frames,
        frames_per_block,
        reserved,
    ) = struct.unpack_from("<4sBBHIIHH", view, 0)
    if magic != FILE_MAGIC:
        raise InvalidContainer(f"bad file magic: {magic!r}")
    if version != FORMAT_VERSION:
        raise InvalidContainer(f"unsupported container version: {version}")
    if not 1 <= channels <= 255:
        raise InvalidContainer(f"invalid channel count: {channels}")
    if header_size != FILE_HEADER_SIZE:
        raise InvalidContainer(f"unsupported file header size: {header_size}")
    if reserved != 0:
        raise InvalidContainer("non-zero reserved file header field")
    if not 1 <= frames_per_block <= 65535:
        raise InvalidContainer(f"invalid frames_per_block: {frames_per_block}")
    if _payload_size(frames_per_block, channels) > 65535:
        raise InvalidContainer("block layout exceeds the payload length field")

    block_count = (total_frames + frames_per_block - 1) // frames_per_block
    full_block_size = block_size(frames_per_block, channels)
    return FileHeader(
        channels=channels,
        sample_rate=sample_rate,
        total_frames=total_frames,
        frames_per_block=frames_per_block,
        header_size=header_size,
        block_count=block_count,
        full_block_size=full_block_size,
    )


def block_offset(header: FileHeader, block_index: int) -> int:
    """Byte offset of a block, available without decoding earlier blocks."""

    if not 0 <= block_index < header.block_count:
        raise InvalidContainer(
            f"block index {block_index} out of range 0..{header.block_count - 1}"
        )
    return header.header_size + block_index * header.full_block_size


def _block_frames(header: FileHeader, block_index: int) -> int:
    frame_start = block_index * header.frames_per_block
    return min(header.frames_per_block, header.total_frames - frame_start)


def _decode_block_at(
    data: bytes | bytearray | memoryview,
    file_header: FileHeader,
    block_index: int,
    *,
    verify_crc: bool,
    collect_trace: bool,
) -> DecodedBlock:
    offset = block_offset(file_header, block_index)
    parsed = parse_block_header(data, offset=offset)
    expected_frames = _block_frames(file_header, block_index)
    if parsed.channels != file_header.channels:
        raise InvalidBlock(
            f"block {block_index} has {parsed.channels} channels, "
            f"container declares {file_header.channels}"
        )
    if parsed.frame_count != expected_frames:
        raise InvalidBlock(
            f"block {block_index} declares {parsed.frame_count} frames, "
            f"container layout expects {expected_frames}"
        )
    return decode_block(
        data,
        offset=offset,
        verify_crc=verify_crc,
        collect_trace=collect_trace,
    )


def decode_block_at(
    data: bytes | bytearray | memoryview,
    block_index: int,
    *,
    verify_crc: bool = True,
    collect_trace: bool = False,
) -> DecodedBlock:
    """Randomly decode block ``block_index`` without touching other blocks."""

    file_header = parse_file_header(data)
    return _decode_block_at(
        data,
        file_header,
        block_index,
        verify_crc=verify_crc,
        collect_trace=collect_trace,
    )


def decode_file(
    data: bytes | bytearray | memoryview,
    *,
    verify_crc: bool = True,
    collect_trace: bool = False,
    strict: bool = True,
) -> Audio:
    """Decode all blocks, truncating output to the declared frame count.

    With ``strict=False``, a bad block is replaced by silence of exactly the
    block's declared duration.  The next block is still decoded from its own
    header state, so corruption cannot propagate.
    """

    file_header = parse_file_header(data)
    decoded_frames: list[list[int]] = [
        [] for _ in range(file_header.channels)
    ]
    failures: list[BlockFailure] = []
    traces: list[tuple[StepTrace, ...]] = []
    for block_index in range(file_header.block_count):
        frame_start = block_index * file_header.frames_per_block
        expected_frames = _block_frames(file_header, block_index)
        try:
            decoded = _decode_block_at(
                data,
                file_header,
                block_index,
                verify_crc=verify_crc,
                collect_trace=collect_trace,
            )
            for channel, samples in enumerate(decoded.samples):
                decoded_frames[channel].extend(samples)
            traces.append(decoded.trace)
        except CodecError as error:
            if strict:
                raise
            for channel in range(file_header.channels):
                decoded_frames[channel].extend([0] * expected_frames)
            failures.append(
                BlockFailure(
                    block_index=block_index,
                    frame_start=frame_start,
                    frame_count=expected_frames,
                    message=str(error),
                )
            )
            traces.append(())

    # Header lengths are authoritative.  This also drops any pad bits and any
    # incomplete trailing frame if a caller concatenates partial data.
    for channel in range(file_header.channels):
        decoded_frames[channel] = decoded_frames[channel][: file_header.total_frames]
    return Audio(
        sample_rate=file_header.sample_rate,
        channels=file_header.channels,
        frames=tuple(tuple(channel) for channel in decoded_frames),
        failures=tuple(failures),
        traces=tuple(traces),
    )
