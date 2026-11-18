#!/usr/bin/env python3
"""End-to-end check: repage the generated Vorbis test files at several
target page sizes and verify, for each output:
  * every page CRC is valid and continued flags are consistent
  * the packet stream is byte-identical to the input
  * per-packet granule positions are preserved
  * BOS/EOS flags are preserved
  * max page size respects the target
  * decoded PCM (libsndfile, independent decoder) is bit-identical
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ogg_repage as R
from verify_pcm import decode

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "out")

INPUTS = ("test_input.ogg", "test_input_255.ogg")
TARGETS = (300, 900, 4096, 65536)


def check_one(src, target):
    dst = src.replace(".ogg", f"_r{target}.ogg")
    trace = dst.replace(".ogg", "_trace.csv")
    stats = R.repage(src, dst, target, trace_path=trace)

    src_pages = R.parse_pages(open(src, "rb").read())
    src_packets, src_serial = R.extract_packets(src_pages)
    R.recover_granules(src_packets)  # true granule of every packet
    dst_pages = R.parse_pages(open(dst, "rb").read())  # CRC validated
    dst_packets, dst_serial = R.extract_packets(dst_pages)

    assert src_serial == dst_serial, "serial changed"
    assert len(src_packets) == len(dst_packets), "packet count changed"
    for a, b in zip(src_packets, dst_packets):
        assert a.data == b.data, "packet payload changed"
    # every output page must carry the true granule of the last packet
    # ending on it (-1 when no packet ends there)
    last_on_page = {}
    for i, pkt in enumerate(dst_packets):
        last_on_page[pkt.orig_last_page] = i
    for seq, page in enumerate(dst_pages):
        if seq in last_on_page:
            want = src_packets[last_on_page[seq]].granule
        else:
            want = R.NO_GRANULE
        assert page.granule == want, (seq, page.granule, want)
    # stream-level granule positions (length / EOS trim) preserved
    assert src_pages[-1].granule == dst_pages[-1].granule
    assert dst_pages[0].header_type & R.BOS
    assert dst_pages[-1].header_type & R.EOS
    assert all(not (p.header_type & R.EOS) for p in dst_pages[:-1])
    cap = max(283, target)
    assert stats["new_max_page"] <= cap, stats["new_max_page"]

    sr_a, pcm_a = decode(src)
    sr_b, pcm_b = decode(dst)
    assert sr_a == sr_b and pcm_a.shape == pcm_b.shape
    assert (pcm_a == pcm_b).all(), "PCM differs"
    return stats


def main():
    print(f"{'input':<22}{'target':>7} {'pages':>13} {'max page':>15} "
          f"{'PCM':>6}")
    for name in INPUTS:
        src = os.path.join(OUT, name)
        for target in TARGETS:
            stats = check_one(src, target)
            pages = f"{stats['old_pages']} -> {stats['new_pages']}"
            maxp = f"{stats['old_max_page']} -> {stats['new_max_page']}"
            print(f"{name:<22}{target:>7} {pages:>13} {maxp:>15} "
              f"{'identical':>6}")
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
