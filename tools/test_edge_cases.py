#!/usr/bin/env python3
"""Edge-case tests for ogg_repage: continued packets spanning pages and
packets whose length is an exact multiple of 255 bytes."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ogg_repage import (BOS, EOS, NO_GRANULE, build_pages, extract_packets,
                        packet_lacing, parse_pages, serialize_pages)


def make_packet(idx, size):
    pkt = type("P", (), {})()
    pkt.data = bytes((idx * 131 + i) % 251 for i in range(size))
    pkt.granule = (idx + 1) * 1000
    pkt.orig_first_page = pkt.orig_last_page = idx  # 1 packet per page
    pkt.new_first_page = pkt.new_last_page = -1
    pkt.ends_eos = False
    return pkt


def last_packets_per_page(packets):
    count = max(p.new_last_page for p in packets) + 1
    per_page = [None] * count
    for pkt in packets:
        per_page[pkt.new_last_page] = pkt
    return per_page


def roundtrip(packets, target_size):
    packets[-1].ends_eos = True
    pages = build_pages(packets, serial=0xDEAD, target_size=target_size,
                        header_packets=0, group_atomic=False)
    blob, sizes = serialize_pages(pages, serial=0xDEAD)
    parsed = parse_pages(blob)                # validates every page CRC
    back, serial = extract_packets(parsed)    # validates continued flags
    assert serial == 0xDEAD
    assert len(back) == len(packets)
    for orig, got in zip(packets, back):
        assert orig.data == got.data, "packet payload corrupted"
    # granule of each page must equal the granule of the last packet
    # ending on it, or -1 when no packet ends there
    for page, pkt in zip(parsed, last_packets_per_page(packets)):
        want = NO_GRANULE if pkt is None else pkt.granule
        assert page.granule == want, (page.seq, page.granule, want)
    assert parsed[0].header_type & BOS
    assert parsed[-1].header_type & EOS
    return parsed, sizes


def test_sizes():
    assert packet_lacing(0) == [0]
    assert packet_lacing(1) == [1]
    assert packet_lacing(254) == [254]
    assert packet_lacing(255) == [255, 0]      # multiple of 255 needs 0
    assert packet_lacing(510) == [255, 255, 0]
    assert packet_lacing(256) == [255, 1]
    print("packet_lacing: OK")


def test_255_multiples():
    sizes = [0, 1, 254, 255, 256, 509, 510, 511, 765, 1020, 4080]
    for target in (283, 300, 1200, 4096):
        packets = [make_packet(i, s) for i, s in enumerate(sizes)]
        parsed, page_sizes = roundtrip(packets, target)
        assert all(s <= max(283, target) for s in page_sizes), page_sizes
    print("255-multiple packets: OK")


def test_huge_spanning_packet():
    # 65025 = 255*255: fills a maximal page exactly; the terminating 0
    # lacing value must land on a continued page of its own
    packets = [make_packet(0, 65025), make_packet(1, 7)]
    parsed, _ = roundtrip(packets, 27 + 255 + 65025)
    big = [p for p in parsed if sum(p.segments) >= 65025]
    assert len(big) == 1 and big[0].nsegs == 255
    idx = parsed.index(big[0])
    nxt = parsed[idx + 1]
    assert nxt.header_type & 0x01, "page after full page must be continued"
    assert nxt.segments[0] == 0, "terminating zero lacing expected"
    print("huge 255*255 packet spanning pages: OK")


def test_tiny_target_cross_page():
    # every packet is forced to span several pages
    sizes = [300, 1000, 5000, 255, 255 * 3 + 1, 77]
    packets = [make_packet(i, s) for i, s in enumerate(sizes)]
    parsed, _ = roundtrip(packets, 283)  # minimum useful target
    continued = sum(1 for p in parsed if p.header_type & 0x01)
    assert continued >= 5, continued
    # pages that end mid-packet must carry granule -1
    for page in parsed[:-1]:
        if page.segments and page.segments[-1] == 255:
            assert page.granule == NO_GRANULE, page.seq
    print("tiny target cross-page packets: OK")


def main():
    test_sizes()
    test_255_multiples()
    test_huge_spanning_packet()
    test_tiny_target_cross_page()
    print("ALL EDGE-CASE TESTS PASSED")


if __name__ == "__main__":
    main()
