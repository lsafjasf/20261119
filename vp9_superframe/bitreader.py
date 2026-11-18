"""Small MSB-first bit reader for VP9 uncompressed frame headers."""

from __future__ import annotations


class BitReaderError(ValueError):
    pass


class BitReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.bit_offset = 0

    @property
    def remaining_bits(self) -> int:
        return len(self._data) * 8 - self.bit_offset

    def read_bit(self) -> int:
        if self.remaining_bits < 1:
            raise BitReaderError("unexpected end of VP9 frame header")
        byte = self._data[self.bit_offset >> 3]
        shift = 7 - (self.bit_offset & 7)
        self.bit_offset += 1
        return (byte >> shift) & 1

    def read_bits(self, count: int) -> int:
        if count < 0 or self.remaining_bits < count:
            raise BitReaderError("unexpected end of VP9 frame header")
        value = 0
        for _ in range(count):
            value = (value << 1) | self.read_bit()
        return value
