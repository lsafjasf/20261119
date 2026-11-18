"""Big-endian bit reader with H.264 Exp-Golomb helpers."""


class EndOfBitsError(Exception):
    """Raised when a read runs past the end of the available bitstream."""


class BitReader:
    """Read individual bits and coded symbols from a byte buffer.

    Bits are consumed MSB first, matching the H.264 bitstream convention.
    """

    def __init__(self, data: bytes | bytearray | memoryview, start_bit: int = 0):
        self.data = bytes(data)
        self.bit_pos = start_bit
        self.total_bits = len(self.data) * 8

    @classmethod
    def from_bits(cls, other: "BitReader", length_bits: int) -> "BitReader":
        """Create a bounded view of ``length_bits`` from ``other``."""
        view = cls(other.data, other.bit_pos)
        view.total_bits = min(other.bit_pos + length_bits, other.total_bits)
        return view

    def remaining_bits(self) -> int:
        return self.total_bits - self.bit_pos

    def more_data(self) -> bool:
        return self.bit_pos < self.total_bits

    def is_byte_aligned(self) -> bool:
        return self.bit_pos % 8 == 0

    def byte_align(self) -> None:
        """Discard up to seven alignment (zero) bits."""
        remainder = self.bit_pos % 8
        if remainder:
            self.read_bits(8 - remainder)

    def read_bits(self, count: int) -> int:
        if count < 0:
            raise ValueError("count must be non-negative")
        if self.bit_pos + count > self.total_bits:
            raise EndOfBitsError(
                f"need {count} bits at offset {self.bit_pos}, "
                f"only {self.remaining_bits()} available"
            )
        value = 0
        pos = self.bit_pos
        for _ in range(count):
            byte = self.data[pos >> 3]
            bit = (byte >> (7 - (pos & 7))) & 1
            value = (value << 1) | bit
            pos += 1
        self.bit_pos = pos
        return value

    def read_flag(self) -> bool:
        return bool(self.read_bits(1))

    def read_ue(self) -> int:
        """Read an unsigned Exp-Golomb coded value (ue(v))."""
        leading_zeros = 0
        while self.more_data() and self.read_bits(1) == 0:
            leading_zeros += 1
            if leading_zeros > 32:
                raise ValueError("Exp-Golomb code too long")
        if not self.more_data():
            raise EndOfBitsError("truncated Exp-Golomb code")
        suffix = self.read_bits(leading_zeros) if leading_zeros else 0
        return (1 << leading_zeros) - 1 + suffix

    def read_se(self) -> int:
        """Read a signed Exp-Golomb coded value (se(v))."""
        code_num = self.read_ue()
        if code_num % 2 == 0:
            return -(code_num // 2)
        return (code_num + 1) // 2

    def skip_bits(self, count: int) -> None:
        if self.bit_pos + count > self.total_bits:
            raise EndOfBitsError("skip past end of bitstream")
        self.bit_pos += count
