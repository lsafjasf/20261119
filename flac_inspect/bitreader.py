"""MSB-first bit reader for FLAC headers and subframes."""


class BitReaderError(ValueError):
    """Raised when a bitstream field is truncated or malformed."""


class BitReader:
    def __init__(self, data: bytes, bit_position: int = 0):
        self.data = data
        self.bit_position = bit_position

    @property
    def byte_position(self) -> int:
        if self.bit_position % 8:
            raise BitReaderError("bit position is not byte aligned")
        return self.bit_position // 8

    @property
    def remaining_bits(self) -> int:
        return len(self.data) * 8 - self.bit_position

    def read(self, count: int) -> int:
        if count < 0:
            raise BitReaderError("negative bit count")
        if count == 0:
            return 0
        if count > self.remaining_bits:
            raise BitReaderError(
                f"truncated bitstream: need {count} bits at bit {self.bit_position}"
            )

        value = 0
        remaining = count
        while remaining:
            byte_index, bit_offset = divmod(self.bit_position, 8)
            take = min(remaining, 8 - bit_offset)
            shift = 8 - bit_offset - take
            chunk = (self.data[byte_index] >> shift) & ((1 << take) - 1)
            value = (value << take) | chunk
            self.bit_position += take
            remaining -= take
        return value

    def read_signed(self, count: int) -> int:
        if count == 0:
            return 0
        value = self.read(count)
        sign_bit = 1 << (count - 1)
        return value - (1 << count) if value & sign_bit else value

    def read_unary_zeroes(self, limit: int | None = None) -> int:
        count = 0
        while self.read(1) == 0:
            count += 1
            if limit is not None and count > limit:
                raise BitReaderError(f"unary value exceeds limit {limit}")
        return count

    def align_to_byte(self) -> int:
        padding = (-self.bit_position) % 8
        value = self.read(padding) if padding else 0
        return value
