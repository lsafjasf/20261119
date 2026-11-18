"""CRC implementations used by the FLAC frame format."""


def _make_crc_table(width: int, polynomial: int) -> tuple[int, ...]:
    top_bit = 1 << (width - 1)
    mask = (1 << width) - 1
    table = []
    for value in range(256):
        crc = value << (width - 8)
        for _ in range(8):
            crc = ((crc << 1) ^ polynomial) & mask if crc & top_bit else (crc << 1) & mask
        table.append(crc)
    return tuple(table)


_CRC8_TABLE = _make_crc_table(8, 0x07)
_CRC16_TABLE = _make_crc_table(16, 0x8005)


def crc8(data: bytes, initial: int = 0) -> int:
    crc = initial
    for value in data:
        crc = _CRC8_TABLE[crc ^ value]
    return crc


def crc16(data: bytes, initial: int = 0) -> int:
    crc = initial
    for value in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC16_TABLE[(crc >> 8) ^ value]
    return crc
