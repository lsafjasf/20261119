"""ADTS (Audio Data Transport Stream) parser and stream segmenter."""

from .adts import (
    ADTS_HEADER_SIZE_NO_CRC,
    ADTS_HEADER_SIZE_WITH_CRC,
    ADTS_HEADER_SYNC,
    SAMPLE_RATES,
    ADTSHeader,
    ADTSParseError,
    parse_adts_header,
)
from .id3 import ID3Tag, parse_id3v2_header
from .parser import Segment, SegmentIndex, TruncatedFrame, parse_stream

__all__ = [
    "ADTS_HEADER_SIZE_NO_CRC",
    "ADTS_HEADER_SIZE_WITH_CRC",
    "ADTS_HEADER_SYNC",
    "SAMPLE_RATES",
    "ADTSHeader",
    "ADTSParseError",
    "parse_adts_header",
    "ID3Tag",
    "parse_id3v2_header",
    "Segment",
    "SegmentIndex",
    "TruncatedFrame",
    "parse_stream",
]
