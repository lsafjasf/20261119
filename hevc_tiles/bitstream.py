"""Small, strict bit-level helpers used by the HEVC syntax parser."""

from __future__ import annotations

from dataclasses import dataclass


class BitstreamError(ValueError):
    """Raised when a syntax element runs past the available RBSP."""


@dataclass(slots=True)
class BitReader:
    data: bytes
    bitpos: int = 0
    name: str = "RBSP"

    @property
    def bits_left(self) -> int:
        return len(self.data) * 8 - self.bitpos

    @property
    def byte_aligned(self) -> bool:
        return self.bitpos % 8 == 0

    @property
    def bytepos(self) -> int:
        return self.bitpos // 8

    def _require(self, count: int) -> None:
        if count < 0 or self.bitpos + count > len(self.data) * 8:
            raise BitstreamError(
                f"{self.name}: need {count} bits at bit {self.bitpos}, "
                f"only {self.bits_left} remain"
            )

    def read_bit(self) -> int:
        self._require(1)
        byte = self.data[self.bitpos >> 3]
        value = (byte >> (7 - (self.bitpos & 7))) & 1
        self.bitpos += 1
        return value

    def read_bits(self, count: int) -> int:
        self._require(count)
        value = 0
        remaining = count
        while remaining:
            offset = self.bitpos & 7
            take = min(remaining, 8 - offset)
            byte = self.data[self.bitpos >> 3]
            value = (value << take) | ((byte >> (8 - offset - take)) & ((1 << take) - 1))
            self.bitpos += take
            remaining -= take
        return value

    def read_ue(self, *, limit: int = 31) -> int:
        leading_zeroes = 0
        while True:
            if self.bits_left <= 0:
                raise BitstreamError(f"{self.name}: truncated Exp-Golomb code")
            if self.read_bit():
                break
            leading_zeroes += 1
            if leading_zeroes > limit:
                raise BitstreamError(
                    f"{self.name}: Exp-Golomb code exceeds {limit} leading zeroes"
                )
        suffix = self.read_bits(leading_zeroes) if leading_zeroes else 0
        return (1 << leading_zeroes) - 1 + suffix

    def read_se(self, *, limit: int = 31) -> int:
        code_num = self.read_ue(limit=limit)
        magnitude = (code_num + 1) >> 1
        return magnitude if code_num & 1 else -magnitude

    def skip(self, count: int) -> None:
        self._require(count)
        self.bitpos += count

    def align(self) -> int:
        skipped = (-self.bitpos) & 7
        self.skip(skipped)
        return skipped

    def read_byte_alignment(self) -> int:
        """Read alignment_bit_equal_to_one followed by zero bits."""
        start = self.bitpos
        if self.read_bit() != 1:
            raise BitstreamError(f"{self.name}: alignment one bit is missing")
        while not self.byte_aligned:
            if self.read_bit() != 0:
                raise BitstreamError(f"{self.name}: non-zero alignment bit")
        return self.bitpos - start


def ebsp_to_rbsp(data: bytes) -> bytes:
    """Remove H.265 emulation-prevention bytes outside the NAL header."""
    out = bytearray()
    zeroes = 0
    for index, value in enumerate(data):
        if zeroes >= 2 and value == 0x03:
            # A 0x03 after two zeroes is an emulation-prevention byte. It is
            # only valid when a byte follows; preserving a trailing one would
            # silently alter malformed input.
            if index + 1 >= len(data):
                raise BitstreamError("trailing emulation-prevention byte")
            zeroes = 0
            continue
        out.append(value)
        zeroes = zeroes + 1 if value == 0 else 0
    return bytes(out)


def rbsp_to_ebsp(data: bytes) -> bytes:
    """Insert emulation-prevention bytes suitable for an HEVC NAL payload."""
    out = bytearray()
    zeroes = 0
    for value in data:
        if zeroes >= 2 and value <= 0x03:
            out.append(0x03)
            zeroes = 0
        out.append(value)
        zeroes = zeroes + 1 if value == 0 else 0
    return bytes(out)


def ebsp_to_rbsp_index_map(ebsp: bytes) -> list[int]:
    """Return RBSP byte count before each EBSP byte boundary.

    The result has ``len(ebsp) + 1`` entries. Emulation-prevention bytes
    contribute no RBSP byte, so the value is unchanged across them.
    """
    mapping = [0]
    zeroes = 0
    count = 0
    for value in ebsp:
        if not (zeroes >= 2 and value == 0x03):
            count += 1
            zeroes = zeroes + 1 if value == 0 else 0
        else:
            zeroes = 0
        mapping.append(count)
    return mapping


def rbsp_offset_to_ebsp(ebsp: bytes, rbsp_offset: int) -> int:
    mapping = ebsp_to_rbsp_index_map(ebsp)
    for index in range(len(mapping) - 1, -1, -1):
        if mapping[index] == rbsp_offset:
            return index
    raise BitstreamError(
        f"RBSP offset {rbsp_offset} exceeds the EBSP payload"
    )
