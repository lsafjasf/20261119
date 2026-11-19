"""PNG scanline filter strategy optimizer (pure standard library).

The package re-selects one of the five PNG scanline filters (None, Sub, Up,
Average, Paeth) per scanline without touching pixel data, color information or
ancillary chunks.  Interlaced PNGs (Adam7) are explicitly rejected.
"""

from .png import PNGError, UnsupportedInterlaceError, ParsedPNG, parse_png

__all__ = ["PNGError", "UnsupportedInterlaceError", "ParsedPNG", "parse_png"]
__version__ = "1.0.0"
