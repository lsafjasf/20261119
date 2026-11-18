#!/usr/bin/env python3
"""Create a legal Vorbis test file whose comment header packet is an
exact multiple of 255 bytes (and large enough to span pages), by
inserting a padding comment into a copy of test_input.ogg."""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ogg_repage import build_pages, extract_packets, parse_pages, serialize_pages
from verify_pcm import decode

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "out", "test_input.ogg")
DST = os.path.join(HERE, "..", "out", "test_input_255.ogg")
TARGET_COMMENT_SIZE = 255 * 17  # 4335 bytes: multiple of 255, spans pages


def pad_comment_packet(data, target):
    # Vorbis comment packet: \x03vorbis, vendor_len, vendor, count, fields
    assert data.startswith(b"\x03vorbis")
    pos = 7
    (vlen,) = struct.unpack_from("<I", data, pos)
    pos += 4
    vendor = data[pos:pos + vlen]
    pos += vlen
    (count,) = struct.unpack_from("<I", data, pos)
    pos += 4
    fields = []
    for _ in range(count):
        (flen,) = struct.unpack_from("<I", data, pos)
        pos += 4
        fields.append(data[pos:pos + flen])
        pos += flen
    assert pos == len(data) - 1 and data[-1] == 1, "bad comment framing"
    pad_field_len = target - len(data) - 4  # new field: len(4)+payload
    assert pad_field_len >= 11
    fields.append(b"PADDING=" + b"x" * (pad_field_len - 8))
    out = bytearray(b"\x03vorbis")
    out += struct.pack("<I", len(vendor)) + vendor
    out += struct.pack("<I", len(fields))
    for field in fields:
        out += struct.pack("<I", len(field)) + field
    out += b"\x01"  # framing bit
    assert len(out) == target and target % 255 == 0
    return bytes(out)


def main():
    pages = parse_pages(open(SRC, "rb").read())
    packets, serial = extract_packets(pages)
    packets[1].data = pad_comment_packet(packets[1].data,
                                         TARGET_COMMENT_SIZE)
    new_pages = build_pages(packets, serial, target_size=65536,
                            header_packets=3, group_atomic=True)
    blob, _ = serialize_pages(new_pages, serial)
    with open(DST, "wb") as fh:
        fh.write(blob)
    # sanity: the padded file must decode to the same PCM as the source
    sr_a, pcm_a = decode(SRC)
    sr_b, pcm_b = decode(DST)
    assert sr_a == sr_b and pcm_a.shape == pcm_b.shape
    assert (pcm_a == pcm_b).all()
    print(f"wrote {DST}: comment packet = {TARGET_COMMENT_SIZE} bytes "
          f"(255 x {TARGET_COMMENT_SIZE // 255}), PCM unchanged")


if __name__ == "__main__":
    main()
