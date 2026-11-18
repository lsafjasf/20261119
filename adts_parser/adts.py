"""Low-level ADTS fixed/variable header parsing (ISO/IEC 13818-7).

ADTS header bit layout (most-significant bit first)::

    syncword                  12 bits   always 0xFFF
    ID                         1 bit    MPEG version (0 = MPEG-4, 1 = MPEG-2)
    layer                      2 bits   always 0
    protection_absent          1 bit    0 -> 16-bit CRC follows the header
    profile                    2 bits   AAC profile minus 1
    sampling_frequency_index   4 bits   index into SAMPLE_RATES
    private_bit                1 bit
    channel_configuration      3 bits
    original_copy              1 bit
    home                       1 bit
    copyright_id_bit           1 bit
    copyright_id_start         1 bit
    frame_length              13 bits   total frame size including header
    buffer_fullness           11 bits
    number_of_raw_data_blocks   2 bits  raw blocks in frame minus 1

The header is 7 bytes when ``protection_absent`` is 1 and 9 bytes otherwise
(the extra two bytes hold the CRC).  Each ADTS frame carries 1024 raw audio
samples per raw data block, i.e. ``1024 * (num_raw_blocks + 1)`` samples.
"""

from dataclasses import dataclass

ADTS_HEADER_SYNC = 0xFFF
ADTS_HEADER_SIZE_NO_CRC = 7
ADTS_HEADER_SIZE_WITH_CRC = 9
CRC_SIZE = 2

SAMPLES_PER_RAW_BLOCK = 1024

# sampling_frequency_index -> sample rate in Hz; 0xC..0xF are reserved.
SAMPLE_RATES = {
    0x0: 96000,
    0x1: 88200,
    0x2: 64000,
    0x3: 48000,
    0x4: 44100,
    0x5: 32000,
    0x6: 24000,
    0x7: 22050,
    0x8: 16000,
    0x9: 12000,
    0xA: 11025,
    0xB: 8000,
    0xC: 7350,
}


class ADTSParseError(ValueError):
    """Raised when bytes cannot be interpreted as an ADTS header."""


@dataclass(frozen=True)
class ADTSHeader:
    """Parsed fields of a single ADTS frame header."""

    offset: int
    mpeg_version: int          # 0 = MPEG-4, 1 = MPEG-2
    protection_absent: int     # 1 -> no CRC; 0 -> CRC present
    profile: int               # raw 2-bit field (audio object type - 1)
    audio_object_type: int     # profile + 1 (1 = AAC Main, 2 = LC, 3 = SSR)
    sample_rate_index: int
    sample_rate: int
    private_bit: int
    channel_config: int
    frame_length: int          # total frame size in bytes (header + data + CRC)
    buffer_fullness: int
    num_raw_data_blocks: int   # decoded count (field value + 1)
    header_size: int           # 7 without CRC, 9 with CRC
    has_crc: bool
    sample_count: int          # playable samples contained in the frame


def parse_adts_header(buf: bytes | bytearray | memoryview, offset: int = 0) -> ADTSHeader:
    """Parse an ADTS header located at ``buf[offset]``.

    Raises :class:`ADTSParseError` if the sync word is missing, the buffer is
    too short, a reserved sample-rate index is used, or the frame length is
    smaller than the header itself.
    """

    data = bytes(buf)
    if offset < 0 or offset >= len(data):
        raise ADTSParseError(f"header offset {offset} is out of range (size={len(data)})")
    if len(data) - offset < ADTS_HEADER_SIZE_NO_CRC:
        raise ADTSParseError(
            f"truncated header at offset {offset}: need "
            f"{ADTS_HEADER_SIZE_NO_CRC} bytes, have {len(data) - offset}"
        )

    b0, b1, b2, b3, b4, b5, b6 = data[offset : offset + 7]
    syncword = (b0 << 4) | (b1 >> 4)
    if syncword != ADTS_HEADER_SYNC:
        raise ADTSParseError(
            f"bad sync word 0x{syncword:03X} at offset {offset} (expected 0xFFF)"
        )

    mpeg_version = (b1 >> 3) & 0x1
    layer = (b1 >> 1) & 0x3
    if layer != 0:
        raise ADTSParseError(f"ADTS layer field must be 0, got {layer} at offset {offset}")

    protection_absent = b1 & 0x1
    has_crc = protection_absent == 0
    header_size = ADTS_HEADER_SIZE_WITH_CRC if has_crc else ADTS_HEADER_SIZE_NO_CRC
    if len(data) - offset < header_size:
        raise ADTSParseError(
            f"truncated header at offset {offset}: CRC-protected header needs "
            f"{header_size} bytes, have {len(data) - offset}"
        )

    profile_raw = (b2 >> 6) & 0x3
    sample_rate_index = (b2 >> 2) & 0xF
    private_bit = b2 & 0x1
    # channel_configuration: b3 bit 7 is the top bit, b3 bits 6-5 the rest.
    channel_config = ((b3 & 0x7F) >> 5) | ((b3 & 0x80) >> 7)

    frame_length = ((b3 & 0x3) << 11) | (b4 << 3) | (b5 >> 5)
    buffer_fullness = ((b5 & 0x1F) << 6) | (b6 >> 2)
    num_raw_blocks_field = b6 & 0x3

    sample_rate = SAMPLE_RATES.get(sample_rate_index)
    if sample_rate is None:
        raise ADTSParseError(
            f"reserved/unsupported sampling_frequency_index 0x{sample_rate_index:X} "
            f"at offset {offset}"
        )
    if frame_length < header_size:
        raise ADTSParseError(
            f"frame_length {frame_length} smaller than header size {header_size} "
            f"at offset {offset}"
        )

    num_raw_data_blocks = num_raw_blocks_field + 1
    return ADTSHeader(
        offset=offset,
        mpeg_version=mpeg_version,
        protection_absent=protection_absent,
        profile=profile_raw,
        audio_object_type=profile_raw + 1,
        sample_rate_index=sample_rate_index,
        sample_rate=sample_rate,
        private_bit=private_bit,
        channel_config=channel_config,
        frame_length=frame_length,
        buffer_fullness=buffer_fullness,
        num_raw_data_blocks=num_raw_data_blocks,
        header_size=header_size,
        has_crc=has_crc,
        sample_count=num_raw_data_blocks * SAMPLES_PER_RAW_BLOCK,
    )
