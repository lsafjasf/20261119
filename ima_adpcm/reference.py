"""Independent IMA ADPCM reference decoder.

This module intentionally duplicates the table constants and parsing logic
instead of calling :mod:`ima_adpcm.codec`.  It is a differential oracle for
encoded blocks and makes accidental coupling between encoder and verifier
obvious.
"""

from __future__ import annotations

import binascii
from dataclasses import dataclass
import struct


_REF_STEPS = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
    34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130,
    143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449,
    494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411,
    1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026,
    4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
    27086, 29794, 32767,
)
_REF_INDEX_DELTAS = (
    -1, -1, -1, -1, 2, 4, 6, 8,
    -1, -1, -1, -1, 2, 4, 6, 8,
)


class ReferenceError(ValueError):
    pass


@dataclass(frozen=True)
class ReferenceStep:
    frame: int
    channel: int
    code: int
    predictor_before: int
    difference: int
    predictor_after: int
    index_before: int
    index_after: int


@dataclass(frozen=True)
class ReferenceBlock:
    channels: int
    frame_count: int
    samples: tuple[tuple[int, ...], ...]
    steps: tuple[ReferenceStep, ...]


def _clip(value: int) -> int:
    return -32768 if value < -32768 else 32767 if value > 32767 else value


def decode_block_reference(
    data: bytes | bytearray | memoryview,
    *,
    offset: int = 0,
    verify_crc: bool = True,
) -> ReferenceBlock:
    """Decode a complete ``IADB`` block using a separate implementation."""

    raw = bytes(data)
    if offset < 0 or offset + 12 > len(raw):
        raise ReferenceError("truncated fixed block header")
    magic, version, channels, frames, payload_len, reserved = struct.unpack_from(
        "<4sBBHHH", raw, offset
    )
    if magic != b"IADB" or version != 1:
        raise ReferenceError("not an IADB version 1 block")
    if channels < 1 or frames < 1 or reserved != 0:
        raise ReferenceError("invalid block header fields")

    expected_payload = ((frames - 1) * channels + 1) // 2
    if payload_len != expected_payload:
        raise ReferenceError("payload length does not match frame/channel count")
    header_size = 12 + 4 * channels
    total_size = header_size + payload_len + 4
    if offset + total_size > len(raw):
        raise ReferenceError("truncated block")

    stored_crc = struct.unpack_from("<I", raw, offset + header_size + payload_len)[0]
    actual_crc = binascii.crc32(raw[offset : offset + header_size + payload_len]) & 0xFFFFFFFF
    if verify_crc and stored_crc != actual_crc:
        raise ReferenceError(
            f"CRC mismatch: stored 0x{stored_crc:08x}, actual 0x{actual_crc:08x}"
        )

    predictors: list[int] = []
    indices: list[int] = []
    for channel in range(channels):
        state_offset = offset + 12 + channel * 4
        predictor, step_index, state_reserved = struct.unpack_from(
            "<hBB", raw, state_offset
        )
        if step_index > 88 or state_reserved != 0:
            raise ReferenceError("invalid channel state")
        predictors.append(predictor)
        indices.append(step_index)
    samples = [[value] for value in predictors]
    steps: list[ReferenceStep] = []
    payload = raw[offset + header_size : offset + header_size + payload_len]

    coded_sample_count = (frames - 1) * channels
    for position in range(coded_sample_count):
        packed = payload[position // 2]
        code = (packed >> 4) & 0x0F if position % 2 else packed & 0x0F
        frame = 1 + position // channels
        channel = position % channels
        before = predictors[channel]
        index_before = indices[channel]
        step = _REF_STEPS[index_before]
        magnitude = step // 8
        magnitude += step if code & 4 else 0
        magnitude += step // 2 if code & 2 else 0
        magnitude += step // 4 if code & 1 else 0
        difference = -magnitude if code & 8 else magnitude
        after = _clip(before + difference)
        index_after = index_before + _REF_INDEX_DELTAS[code]
        if index_after < 0:
            index_after = 0
        elif index_after > 88:
            index_after = 88
        predictors[channel] = after
        indices[channel] = index_after
        samples[channel].append(after)
        steps.append(
            ReferenceStep(
                frame=frame,
                channel=channel,
                code=code,
                predictor_before=before,
                difference=difference,
                predictor_after=after,
                index_before=index_before,
                index_after=index_after,
            )
        )
    return ReferenceBlock(
        channels=channels,
        frame_count=frames,
        samples=tuple(tuple(channel) for channel in samples),
        steps=tuple(steps),
    )


def verify_against_reference(
    data: bytes | bytearray | memoryview,
    *,
    offset: int = 0,
    verify_crc: bool = True,
) -> int:
    """Compare the production decoder with this reference implementation.

    Returns the number of verified samples.  A mismatch raises
    :class:`ReferenceError`.
    """

    from .codec import decode_block

    expected = decode_block_reference(data, offset=offset, verify_crc=verify_crc)
    actual = decode_block(data, offset=offset, verify_crc=verify_crc)
    if expected.samples != actual.samples:
        raise ReferenceError(
            "core/reference sample mismatch: "
            f"reference={expected.samples!r}, core={actual.samples!r}"
        )
    if expected.channels != actual.channels or expected.frame_count != actual.frame_count:
        raise ReferenceError("core/reference block geometry mismatch")
    return expected.frame_count * expected.channels
