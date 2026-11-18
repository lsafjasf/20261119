"""Generate deterministic ADTS test fixtures without external encoders.

The generated raw_data_block payloads are synthetic filler bytes, which is
fine for header/framing tests: only the transport layer is exercised here.
Run with ``python3 samples/make_samples.py``.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adts_parser.adts import (  # noqa: E402
    ADTS_HEADER_SIZE_NO_CRC,
    ADTS_HEADER_SIZE_WITH_CRC,
    SAMPLE_RATES,
)
from adts_parser.id3 import ID3V2_HEADER_SIZE  # noqa: E402


def encode_synchsafe(value: int) -> bytes:
    return bytes(
        [(value >> 21) & 0x7F, (value >> 14) & 0x7F, (value >> 7) & 0x7F, value & 0x7F]
    )


def make_id3v2(payload: bytes, major_version: int = 4, flags: int = 0) -> bytes:
    header = b"ID3" + bytes([major_version, 0, flags]) + encode_synchsafe(len(payload))
    assert len(header) == ID3V2_HEADER_SIZE
    return header + payload


def make_adts_frame(
    *,
    payload_size: int,
    profile: int = 1,          # 1 = LC in the raw ADTS field (AOT 2)
    sample_rate_index: int = 4,  # 44100 Hz
    channel_config: int = 2,
    protection_absent: int = 1,
    mpeg_version: int = 0,
    fill: int = 0xAA,
) -> bytes:
    """Build one ADTS frame with the requested CRC mode and payload size."""

    has_crc = protection_absent == 0
    header_size = (
        ADTS_HEADER_SIZE_WITH_CRC if has_crc else ADTS_HEADER_SIZE_NO_CRC
    )
    frame_length = header_size + payload_size
    if frame_length > 0x1FFF:
        raise ValueError("frame_length does not fit in 13 bits")
    if sample_rate_index not in SAMPLE_RATES:
        raise ValueError(f"bad sample rate index {sample_rate_index}")

    b0 = 0xFF
    b1 = 0xF0 | ((mpeg_version & 1) << 3) | (protection_absent & 1)
    b2 = ((profile & 0x3) << 6) | ((sample_rate_index & 0xF) << 2)
    b3 = ((channel_config & 0x7) << 5) | ((frame_length >> 11) & 0x3)
    b4 = (frame_length >> 3) & 0xFF
    # buffer_fullness = 0x7FF (VBR) spread over b5 low 5 bits and b6 high 6.
    b5 = ((frame_length & 0x7) << 5) | 0x1F
    b6 = 0xFC  # buffer fullness low 6 bits = 0x3F, num_raw_data_blocks = 0
    header = bytes([b0, b1, b2, b3, b4, b5, b6])
    if has_crc:
        header += b"\x00\x00"  # CRC-16 placeholder (contents irrelevant here)
    return header + bytes([fill]) * payload_size


def main() -> None:
    out_dir = Path(__file__).resolve().parent

    # 1) Single track, no CRC: five 200-byte-payload frames at 44.1 kHz LC.
    no_crc = b"".join(
        make_adts_frame(payload_size=200, protection_absent=1, fill=index)
        for index in range(5)
    )
    (out_dir / "single_no_crc.aac").write_bytes(no_crc)

    # 2) Single track with CRC protection: header must be 9 bytes.
    with_crc = b"".join(
        make_adts_frame(payload_size=128, protection_absent=0, fill=0x40 + index)
        for index in range(4)
    )
    (out_dir / "single_with_crc.aac").write_bytes(with_crc)

    # 3) Mixed CRC modes inside one track.
    mixed = (
        make_adts_frame(payload_size=96, protection_absent=1)
        + make_adts_frame(payload_size=96, protection_absent=0)
        + make_adts_frame(payload_size=96, protection_absent=1)
    )
    (out_dir / "mixed_crc_modes.aac").write_bytes(mixed)

    # 4) Two tracks concatenated, each introduced by its own ID3v2 tag.
    id3_one = make_id3v2(b"TPE1" + b"\x00\x00\x00\x06" + b"\x00\x00" + b"alice!")
    id3_two = make_id3v2(b"TIT2" + b"\x00\x00\x00\x05" + b"\x00\x00" + b"song2")
    track_one = b"".join(
        make_adts_frame(
            payload_size=160, sample_rate_index=4, channel_config=2, fill=0x11
        )
        for _ in range(3)
    )
    track_two = b"".join(
        make_adts_frame(
            payload_size=110, sample_rate_index=8, channel_config=1, fill=0x22
        )
        for _ in range(6)
    )
    (out_dir / "concat_two_tracks_id3.aac").write_bytes(
        id3_one + track_one + id3_two + track_two
    )

    # 5) Same as #4 but with the last frame cut mid-stream.
    full_last = make_adts_frame(
        payload_size=110, sample_rate_index=8, channel_config=1, fill=0x33
    )
    truncated_last = full_last[: 7 + 30]  # header present, payload incomplete
    concat_truncated = id3_one + track_one + id3_two + track_two + truncated_last
    (out_dir / "concat_truncated_tail.aac").write_bytes(concat_truncated)

    # 6) Parameter change (no ID3): adjacent 44.1k stereo -> 16k mono.
    part_a = make_adts_frame(payload_size=150, sample_rate_index=4, channel_config=2)
    part_b = make_adts_frame(payload_size=150, sample_rate_index=8, channel_config=1)
    (out_dir / "param_change_boundary.aac").write_bytes(part_a + part_b)

    print("generated fixtures in", out_dir)
    for path in sorted(out_dir.glob("*.aac")):
        print(f"  {path.name}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
