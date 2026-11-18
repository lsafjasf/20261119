"""Bit-level writer used to synthesise H.264 bitstreams in tests."""


class BitWriter:
    def __init__(self) -> None:
        self._bytes = bytearray()
        self._current = 0
        self._bits = 0

    def write_bits(self, value: int, count: int) -> "BitWriter":
        for shift in range(count - 1, -1, -1):
            self._current = (self._current << 1) | ((value >> shift) & 1)
            self._bits += 1
            if self._bits == 8:
                self._bytes.append(self._current)
                self._current = 0
                self._bits = 0
        return self

    def write_flag(self, flag: bool) -> "BitWriter":
        return self.write_bits(1 if flag else 0, 1)

    def write_ue(self, value: int) -> "BitWriter":
        code_num = value + 1
        length = code_num.bit_length()
        return self.write_bits(0, length - 1).write_bits(code_num, length)

    def write_se(self, value: int) -> "BitWriter":
        if value > 0:
            return self.write_ue(2 * value - 1)
        return self.write_ue(-2 * value)

    def byte_align(self) -> "BitWriter":
        if self._bits:
            return self.write_bits(1, 1).write_bits(0, 8 - self._bits)
        return self

    def rbsp_trailing_bits(self) -> "BitWriter":
        if self._bits == 0:
            return self.write_bits(1, 1).write_bits(0, 7)
        zeros = 7 - self._bits
        return self.write_flag(True).write_bits(0, zeros)

    def to_bytes(self) -> bytes:
        if self._bits:
            raise ValueError("writer is not byte aligned")
        return bytes(self._bytes)


def add_emulation_prevention(rbsp: bytes) -> bytes:
    """Inverse of remove_emulation_prevention, including 00 00 00..03 cases."""
    out = bytearray()
    zero_count = 0
    for byte in rbsp:
        if zero_count >= 2 and byte <= 0x03:
            out.append(0x03)
            zero_count = 0
        out.append(byte)
        if byte == 0:
            zero_count += 1
        else:
            zero_count = 0
    return bytes(out)


def annexb_nal(header: int, rbsp: bytes) -> bytes:
    return b"\x00\x00\x00\x01" + bytes([header]) + add_emulation_prevention(rbsp)
