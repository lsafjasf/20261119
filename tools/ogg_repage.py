#!/usr/bin/env python3
"""Repaginate a single-logical-stream Ogg file to a target page size.

Preserves the logical stream serial number, packet boundaries, granule
positions and BOS/EOS flags; recomputes every page CRC.  Handles packets
spanning pages (continued packets) and packets whose length is an exact
multiple of 255 bytes (which require a terminating zero lacing value).

Usage:
    ogg_repage.py INPUT OUTPUT --target-size N [--trace TRACE.csv]
"""

import argparse
import csv
import struct
import sys

# Ogg CRC-32: polynomial 0x04C11DB7, MSB-first, init 0, no final xor.
_CRC_TABLE = []
for _i in range(256):
    _r = _i << 24
    for _ in range(8):
        _r = ((_r << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if _r & 0x80000000 \
            else (_r << 1) & 0xFFFFFFFF
    _CRC_TABLE.append(_r)


def ogg_crc(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[((crc >> 24) & 0xFF) ^ byte]
    return crc


CONTINUED = 0x01
BOS = 0x02
EOS = 0x04
NO_GRANULE = 0xFFFFFFFFFFFFFFFF  # -1: no packet ends on this page


class Page:
    __slots__ = ("header_type", "granule", "serial", "seq", "crc",
                 "segments", "body", "offset", "size")

    @property
    def nsegs(self):
        return len(self.segments)

    @property
    def continued(self):
        return bool(self.header_type & CONTINUED)


def parse_pages(data: bytes):
    """Parse all Ogg pages in *data*; validates CRC and structure."""
    pages = []
    off = 0
    while off < len(data):
        if data[off:off + 4] != b"OggS":
            raise ValueError(f"bad capture pattern at offset {off}")
        if data[off + 4] != 0:
            raise ValueError(f"unsupported stream version at offset {off}")
        header_type = data[off + 5]
        granule, serial, seq, crc = struct.unpack_from("<qIIi", data, off + 6)
        granule &= 0xFFFFFFFFFFFFFFFF
        nsegs = data[off + 26]
        seg_off = off + 27
        segments = list(data[seg_off:seg_off + nsegs])
        body_len = sum(segments)
        body_off = seg_off + nsegs
        page_bytes = data[off:body_off + body_len]
        # verify CRC with the crc field zeroed
        chk = bytearray(page_bytes)
        chk[22:26] = b"\x00\x00\x00\x00"
        if ogg_crc(bytes(chk)) != crc & 0xFFFFFFFF:
            raise ValueError(f"CRC mismatch on page seq={seq} at offset {off}")
        page = Page()
        page.header_type = header_type
        page.granule = granule
        page.serial = serial
        page.seq = seq
        page.crc = crc & 0xFFFFFFFF
        page.segments = segments
        page.body = data[body_off:body_off + body_len]
        page.offset = off
        page.size = len(page_bytes)
        pages.append(page)
        off += len(page_bytes)
    return pages


class Packet:
    __slots__ = ("data", "granule", "orig_first_page", "orig_last_page",
                 "new_first_page", "new_last_page", "ends_eos")


def extract_packets(pages):
    """Reassemble packets from pages.

    Each packet records the granule position of the original page on which
    it ended; that granule is re-attached to the new page where the packet
    ends during repagination.  Pages on which no packet ends carry -1.
    """
    if not pages:
        raise ValueError("no pages in stream")
    serial = pages[0].serial
    for page in pages:
        if page.serial != serial:
            raise ValueError("multiplexed streams are not supported")
    if not pages[0].header_type & BOS:
        raise ValueError("first page is not a BOS page")

    packets = []
    pending = bytearray()      # bytes of an unfinished packet
    pending_first = 0          # original page index where it started
    have_pending = False
    prev_open = False          # previous page ended mid-packet

    for idx, page in enumerate(pages):
        if page.continued != prev_open:
            raise ValueError(
                f"continued-flag mismatch on page seq={page.seq}")
        pos = 0
        if not page.continued:
            pending = bytearray()
            pending_first = idx
        for seg in page.segments:
            pending += page.body[pos:pos + seg]
            pos += seg
            have_pending = True
            if seg < 255:  # packet terminates here
                pkt = Packet()
                pkt.data = bytes(pending)
                pkt.granule = page.granule
                pkt.orig_first_page = pending_first
                pkt.orig_last_page = idx
                pkt.new_first_page = pkt.new_last_page = -1
                pkt.ends_eos = False
                packets.append(pkt)
                pending = bytearray()
                pending_first = idx
                have_pending = False
        prev_open = have_pending
    if prev_open:
        raise ValueError("stream ends with an unfinished packet")
    if not pages[-1].header_type & EOS:
        raise ValueError("last page is not an EOS page")
    packets[-1].ends_eos = True
    return packets, serial


def packet_lacing(length: int):
    """Lacing values for a packet; exact multiples of 255 (and zero-length
    packets) need a terminating 0 lacing value."""
    segs = [255] * (length // 255)
    segs.append(length % 255)
    return segs


def build_pages(packets, serial, target_size, header_packets=3,
                group_atomic=False):
    """Re-emit packets as pages not exceeding *target_size* bytes.

    * granule position of a page = granule of the last packet ending on it,
      or -1 when no packet ends there;
    * the continued flag is set whenever a packet spans a page boundary;
    * BOS stays on the first page, EOS on the page where the original
      EOS packet ends;
    * Vorbis header grouping is preserved: header packet 0 sits alone on
      the first page and no audio packet shares a page with the last
      header packet.
    """
    # capacity counts body bytes plus one byte per segment-table entry,
    # so the total page size (27-byte header + table + body) stays within
    # the target; a page must always hold at least one full segment
    cap_bytes = max(256, target_size - 27)
    cap_segs = 255

    pages = []          # list of dicts under construction
    cur = None

    def new_page(continued):
        nonlocal cur
        cur = {"continued": continued, "segments": [], "body": bytearray(),
               "granule": None, "eos": False}
        pages.append(cur)

    def flush_room(seg_len):
        used = len(cur["body"]) + len(cur["segments"])
        return (len(cur["segments"]) < cap_segs
                and used + seg_len + 1 <= cap_bytes)

    new_page(continued=False)

    if group_atomic:
        # granule positions are only known for the last packet of each
        # original page, so every group of packets that shared an original
        # page must still end together on one new page
        groups = []
        for pkt in packets:
            if groups and groups[-1][-1].orig_last_page == pkt.orig_last_page:
                groups[-1].append(pkt)
            else:
                groups.append([pkt])
        pkt_index = 0
        for members in groups:
            cost = sum(len(packet_lacing(len(p.data))) +
                       len(p.data) for p in members)
            nsegs = sum(len(packet_lacing(len(p.data))) for p in members)
            if cost > cap_bytes or nsegs > cap_segs:
                raise RuntimeError(
                    "a packet group ending on original page "
                    f"{members[0].orig_last_page} needs {cost} bytes/"
                    f"{nsegs} segments, more than a fresh page holds; "
                    "use granule recovery (default) for small targets")
        for members in groups:
            cost = sum(len(packet_lacing(len(p.data))) +
                       len(p.data) for p in members)
            nsegs = sum(len(packet_lacing(len(p.data))) for p in members)
            used = len(cur["body"]) + len(cur["segments"])
            if used and (used + cost > cap_bytes
                         or len(cur["segments"]) + nsegs > cap_segs):
                new_page(continued=False)
            for pkt in members:
                lacing = packet_lacing(len(pkt.data))
                pos = 0
                for seg in lacing:
                    assert flush_room(seg)
                    cur["segments"].append(seg)
                    cur["body"] += pkt.data[pos:pos + seg]
                    pos += seg
                cur["granule"] = pkt.granule
                pkt.new_last_page = len(pages) - 1
                if pkt.new_first_page == -1:
                    pkt.new_first_page = pkt.new_last_page
                if pkt.ends_eos:
                    cur["eos"] = True
                pkt_index += 1
            # Vorbis header grouping, as in the recovery path below
            if header_packets and pkt_index in (1, header_packets) \
                    and pkt_index != len(packets):
                new_page(continued=False)
        return pages
    for idx, pkt in enumerate(packets):
        lacing = packet_lacing(len(pkt.data))
        pos = 0
        first_seg = True
        for seg in lacing:
            if not flush_room(seg):
                # page is full: close it and start a new one; the new page
                # is "continued" only if part of this packet was already
                # written to the previous page
                new_page(continued=not first_seg)
            cur["segments"].append(seg)
            cur["body"] += pkt.data[pos:pos + seg]
            pos += seg
            if first_seg:
                pkt.new_first_page = len(pages) - 1
                first_seg = False
        # packet ends on the current page
        cur["granule"] = pkt.granule
        pkt.new_last_page = len(pages) - 1
        if pkt.ends_eos:
            cur["eos"] = True
        # Vorbis header grouping: id header alone on page 0; the page
        # holding the end of the last header packet carries no audio.
        if header_packets and (idx == 0 or idx == header_packets - 1) \
                and idx != len(packets) - 1:
            new_page(continued=False)
    return pages


def serialize_pages(pages, serial):
    """Render page dicts to bytes, assigning sequence numbers and CRCs."""
    out = bytearray()
    sizes = []
    for seq, page in enumerate(pages):
        header_type = 0
        if page["continued"]:
            header_type |= CONTINUED
        if seq == 0:
            header_type |= BOS
        if page["eos"]:
            header_type |= EOS
        granule = page["granule"] if page["granule"] is not None else NO_GRANULE
        header = struct.pack(
            "<4sBBqIII", b"OggS", 0, header_type,
            struct.unpack("<q", struct.pack("<Q", granule))[0],
            serial, seq, 0)
        header += bytes([len(page["segments"])])
        header += bytes(page["segments"])
        crc = ogg_crc(header + bytes(page["body"]))
        header = header[:22] + struct.pack("<I", crc) + header[26:]
        out += header
        out += page["body"]
        sizes.append(len(header) + len(page["body"]))
    return bytes(out), sizes


def write_trace(path, packets):
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "packet", "bytes", "lacing_segments", "multiple_of_255",
            "spans_pages_orig", "orig_first_page", "orig_last_page",
            "spans_pages_new", "new_first_page", "new_last_page",
            "granule", "eos",
        ])
        for idx, pkt in enumerate(packets):
            writer.writerow([
                idx, len(pkt.data), len(packet_lacing(len(pkt.data))),
                int(len(pkt.data) % 255 == 0),
                int(pkt.orig_first_page != pkt.orig_last_page),
                pkt.orig_first_page, pkt.orig_last_page,
                int(pkt.new_first_page != pkt.new_last_page),
                pkt.new_first_page, pkt.new_last_page,
                pkt.granule, int(pkt.ends_eos),
            ])


def recover_granules(packets):
    """Assign the true granule position to every packet using libvorbis.

    Original page granules stay authoritative for the last packet ending
    on each original page (this preserves EOS end-trimming); the recovered
    values fill in all intermediate packets.
    """
    from vorbis_pcm import decode_vorbis_packets, is_vorbis
    datas = [pkt.data for pkt in packets]
    if not is_vorbis(datas):
        raise RuntimeError("stream is not Vorbis; cannot recover granules")
    _, _, granules, _ = decode_vorbis_packets(datas)
    group_final = set()
    for idx, pkt in enumerate(packets):
        if idx + 1 == len(packets) or \
                packets[idx + 1].orig_last_page != pkt.orig_last_page:
            group_final.add(idx)
    for idx, pkt in enumerate(packets):
        if idx in group_final:
            continue  # keep the original page granule
        recovered = granules[idx]
        pkt.granule = 0 if recovered is None else recovered


def repage(input_path, output_path, target_size, trace_path=None,
           header_packets=3, granule_mode="auto"):
    with open(input_path, "rb") as fh:
        data = fh.read()
    pages = parse_pages(data)
    packets, serial = extract_packets(pages)
    if granule_mode == "auto":
        try:
            from vorbis_pcm import is_vorbis
            recover = is_vorbis([pkt.data for pkt in packets])
        except Exception:
            recover = False
    else:
        recover = granule_mode == "recover"
    if recover:
        recover_granules(packets)
    new_pages = build_pages(packets, serial, target_size,
                            header_packets=header_packets,
                            group_atomic=not recover)
    blob, new_sizes = serialize_pages(new_pages, serial)
    with open(output_path, "wb") as fh:
        fh.write(blob)
    if trace_path:
        write_trace(trace_path, packets)
    old_sizes = [page.size for page in pages]
    return {
        "packets": len(packets),
        "old_pages": len(pages),
        "new_pages": len(new_pages),
        "old_max_page": max(old_sizes),
        "new_max_page": max(new_sizes),
        "old_bytes": len(data),
        "new_bytes": len(blob),
        "serial": serial,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--target-size", type=int, default=4096,
                        help="target maximum page size in bytes "
                             "(default: 4096)")
    parser.add_argument("--trace", help="write packet-level trace CSV")
    parser.add_argument("--no-header-grouping", action="store_true",
                        help="do not keep Vorbis header packets on "
                             "their own pages")
    parser.add_argument("--granule-mode",
                        choices=("auto", "recover", "preserve"),
                        default="auto",
                        help="auto (default): recover per-packet granule "
                             "positions via libvorbis when possible; "
                             "preserve: container-only, keeps original "
                             "page granule groups unsplit")
    args = parser.parse_args(argv)
    stats = repage(args.input, args.output, args.target_size,
                   trace_path=args.trace,
                   header_packets=0 if args.no_header_grouping else 3,
                   granule_mode=args.granule_mode)
    print(f"serial           : 0x{stats['serial']:08x}")
    print(f"packets          : {stats['packets']}")
    print(f"pages            : {stats['old_pages']} -> {stats['new_pages']}")
    print(f"max page size    : {stats['old_max_page']} -> "
          f"{stats['new_max_page']} bytes")
    print(f"file size        : {stats['old_bytes']} -> "
          f"{stats['new_bytes']} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
