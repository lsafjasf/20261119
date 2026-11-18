"""Annex B start-code splitting and emulation prevention handling."""


def split_annex_b(stream: bytes | bytearray) -> list[bytes]:
    """Split an Annex B byte stream into NAL unit byte strings.

    Both three-byte (``00 00 01``) and four-byte (``00 00 00 01``) start
    code prefixes are recognised.  Bytes before the first start code and
    trailing zero padding are ignored.
    """
    data = bytes(stream)
    nals: list[bytes] = []
    ranges = annex_b_ranges(data)
    for index, (_sc_start, payload_start, end) in enumerate(ranges):
        nal = data[payload_start:end]
        # Between two start codes everything after the NAL's rbsp_trailing
        # bits is zero padding (Annex B).  The last NAL reaches EOF and must
        # never be trimmed, since a truncated stream can end in zeros.
        if index + 1 < len(ranges):
            nal = nal.rstrip(b"\x00")
        if nal:
            nals.append(nal)
    return nals


def annex_b_ranges(stream: bytes | bytearray) -> list[tuple[int, int, int]]:
    """Return ``(start_code_offset, payload_offset, payload_end)`` per NAL."""
    data = bytes(stream)
    ranges: list[tuple[int, int, int]] = []
    i = 0
    length = len(data)
    sc_start = -1
    while i < length - 2:
        if data[i] == 0 and data[i + 1] == 0:
            if data[i + 2] == 1:
                sc_start, payload_start = i, i + 3
                i += 3
            elif i + 3 < length and data[i + 3] == 1:
                sc_start, payload_start = i, i + 4
                i += 4
            else:
                i += 1
                continue
            # Walk the NAL body, skipping 00 00 03 emulation prevention
            # triples so they cannot be mistaken for a new start code.
            j = payload_start
            next_sc = length
            while j < length - 2:
                if data[j] == 0 and data[j + 1] == 0:
                    if j + 2 < length and data[j + 2] == 0x03:
                        j += 3
                        continue
                    if data[j + 2] == 1 or (
                        j + 3 < length and data[j + 3] == 1
                    ):
                        next_sc = j
                        break
                j += 1
            ranges.append((sc_start, payload_start, next_sc))
            i = next_sc
        else:
            i += 1
    return ranges


def remove_emulation_prevention(ebsp: bytes | bytearray) -> bytes:
    """Convert an EBSP (NAL unit body) into an RBSP.

    Every ``00 00 03`` sequence loses its ``03`` emulation prevention
    byte.  A malformed prevention sequence (``00 00`` followed by a byte
    other than 0..3) is left untouched so callers can keep the original
    payload instead of crashing.
    """
    data = bytes(ebsp)
    out = bytearray()
    i = 0
    length = len(data)
    while i < length:
        if (
            i + 2 < length
            and data[i] == 0
            and data[i + 1] == 0
            and data[i + 2] == 0x03
        ):
            out.append(0)
            out.append(0)
            i += 3
        else:
            out.append(data[i])
            i += 1
    return bytes(out)
