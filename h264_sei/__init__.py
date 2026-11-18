"""H.264 (AVC) SEI NAL extraction and parsing toolkit.

The package only relies on the Python standard library.  It splits an
Annex B byte stream into NAL units, groups them into access units and
parses SEI messages (buffering period, picture timing, user data
unregistered and recovery point), preserving raw payload bytes for every
message - including unknown or malformed ones.
"""

from .bitreader import BitReader, EndOfBitsError
from .rbsp import remove_emulation_prevention, split_annex_b
from .sei import (
    SEI_MESSAGE_TYPES,
    SeiMessage,
    parse_sei_rbsp,
)
from .stream import AccessUnit, NalUnit, extract_access_units

__all__ = [
    "BitReader",
    "EndOfBitsError",
    "remove_emulation_prevention",
    "split_annex_b",
    "SEI_MESSAGE_TYPES",
    "SeiMessage",
    "parse_sei_rbsp",
    "AccessUnit",
    "NalUnit",
    "extract_access_units",
]

__version__ = "1.0.0"
