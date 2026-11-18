"""End-to-end demonstrations for the IMA ADPCM implementation."""

from __future__ import annotations

import struct
import zlib

from .codec import (
    STEP_TABLE,
    CrcMismatch,
    block_offset,
    decode_block,
    decode_block_at,
    decode_file,
    encode_block,
    encode_file,
    parse_block_header,
    parse_file_header,
)
from .metrics import compare_samples
from .reference import verify_against_reference


def _roundtrip(signal: list[int], frames_per_block: int = 256) -> list[int]:
    encoded = encode_file(
        [signal], sample_rate=8_000, frames_per_block=frames_per_block
    )
    return list(decode_file(encoded).frames[0])


def _demo_signals(size: int = 4_096) -> dict[str, list[int]]:
    silence = [0] * size
    step = [-32768 if frame < 999 else 32767 for frame in range(size)]
    ramp = [
        round(-30000 + 60000 * frame / (size - 1))
        for frame in range(size)
    ]
    return {"silence": silence, "full-scale step": step, "slow ramp": ramp}


def print_layout() -> None:
    print("Block layout (all integers little-endian)")
    print("  0   magic 'IADB'             4 bytes")
    print("  4   version uint8 = 1        1 byte")
    print("  5   channels uint8           1 byte")
    print("  6   frame_count uint16       2 bytes (header predictor is frame 0)")
    print("  8   payload_size uint16      2 bytes")
    print(" 10   reserved uint16 = 0      2 bytes")
    print(" 12   per-channel states       4 bytes each: int16 predictor, uint8 index, uint8 0")
    print("  .   packed 4-bit codes       low nibble first, then high nibble")
    print("last  CRC32 uint32             4 bytes, over every preceding block byte")
    print()


def print_trace() -> None:
    # Hand-checkable vector: codes 7, 0, 8, 15 -> bytes 07 f8.
    source = [0, 11, 13, 12, -11]
    encoded = encode_block([source])
    decoded = decode_block(encoded, collect_trace=True)
    payload = encoded[
        parse_block_header(encoded).header_size :
        parse_block_header(encoded).header_size
        + parse_block_header(encoded).payload_size
    ]
    print("Step-by-step trace: header frame is lossless; each row decodes one nibble")
    state = decoded.initial_states[0]
    print(
        f"  frame=0 channel=0 header predictor={state.predictor} "
        f"step_index={state.step_index} step_size={STEP_TABLE[state.step_index]}"
    )
    print("  frame ch code  prediction  quantized_diff  reconstructed  index")
    for event in decoded.trace:
        print(
            f"  {event.frame:5d} {event.channel:2d}  {event.code:1x}   "
            f"{event.prediction:10d} {event.quantized_difference:15d} "
            f"{event.reconstructed:14d}  {event.step_index_before:2d}->"
            f"{event.step_index_after:2d}"
        )
    print(
        f"  packed payload: {payload.hex(' ')}  "
        "(nibble order: frame 1/ch 0 is the low nibble)"
    )
    print(f"  decoded PCM: {decoded.interleaved()}")
    print()


def print_errors() -> None:
    print("Error comparison (one channel, 256 frames/block, headers reset state)")
    print("  signal             samples       RMSE        MAE      max   SNR(dB)")
    for name, source in _demo_signals().items():
        decoded = _roundtrip(source)
        metrics = compare_samples(source, decoded)
        print(f"  {name:18s}{metrics.as_row()}")
    print()


def print_isolation() -> None:
    frames_per_block = 32
    blocks = 4
    source = [
        ((frame * 811) % 48001) - 24000
        for frame in range(blocks * frames_per_block)
    ]
    encoded = encode_file(
        [source], sample_rate=8_000, frames_per_block=frames_per_block
    )
    file_header = parse_file_header(encoded)
    next_before = decode_block_at(encoded, 1, verify_crc=True).samples[0]

    damaged = bytearray(encoded)
    first_block = parse_block_header(encoded, offset=file_header.header_size)
    damaged[file_header.header_size + first_block.header_size] ^= 0x5A
    next_after = decode_block_at(bytes(damaged), 1, verify_crc=True).samples[0]
    recovered = decode_file(bytes(damaged), strict=False)

    block0_start = 0
    block1_start = frames_per_block
    expected_block1 = tuple(
        recovered.frames[0][block1_start : 2 * frames_per_block]
    )
    print("Corruption isolation")
    print(f"  block 1 before corruption == after corruption: {next_before == next_after}")
    print(
        "  salvage decode block 1 still matches source: "
        f"{next_before == expected_block1}"
    )
    print(
        f"  reported failures: {len(recovered.failures)} "
        f"(block {recovered.failures[0].block_index}, frames "
        f"{recovered.failures[0].frame_start}.."
        f"{recovered.failures[0].frame_start + recovered.failures[0].frame_count - 1})"
    )
    try:
        decode_file(bytes(damaged), strict=True)
    except CrcMismatch:
        print("  strict decode rejects the damaged block with CrcMismatch")
    assert next_before == next_after
    assert recovered.failures[0].block_index == 0
    print()


def print_tail_handling() -> None:
    frames_per_block = 64
    source = list(range(1, 4 * frames_per_block + 2))
    encoded = encode_file(
        [source], sample_rate=8_000, frames_per_block=frames_per_block
    )
    decoded = decode_file(encoded)
    print("Tail and padding")
    print(
        f"  odd total frame count {len(source)} -> decoded "
        f"{decoded.frame_count} frames (header is authoritative)"
    )

    padded = bytearray(encode_block([[0, 100, 200, 300]]))
    header = parse_block_header(padded)
    payload_offset = header.header_size
    padded[payload_offset] |= 0xF0
    crc_offset = payload_offset + header.payload_size
    struct.pack_into("<I", padded, crc_offset, zlib.crc32(padded[:crc_offset]))
    padded_decoded = decode_block(bytes(padded))
    print(
        f"  non-zero pad nibble still yields {padded_decoded.frame_count} frames: "
        f"{padded_decoded.interleaved()}"
    )
    print()


def print_reference_verification() -> None:
    source = [
        [0, 200, 700, -300, 900, 1200, -1500, 32767],
        [0, -200, -700, 300, -900, -1200, 1500, -32768],
    ]
    block = encode_block(source)
    verified = verify_against_reference(block)

    container = encode_file(source, sample_rate=8_000, frames_per_block=4)
    file_header = parse_file_header(container)
    for index in range(file_header.block_count):
        verify_against_reference(container, offset=block_offset(file_header, index))
    print("Independent reference decoder")
    print(f"  production and reference agree on {verified} stereo samples")
    print(f"  all {file_header.block_count} random-access blocks verified")
    print()


def main() -> int:
    print("IMA ADPCM/16 codec demonstration")
    print("=" * 68)
    print_layout()
    print_trace()
    print_errors()
    print_isolation()
    print_tail_handling()
    print_reference_verification()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
