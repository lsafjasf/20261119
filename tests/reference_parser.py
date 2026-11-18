"""Independent reference implementation for cross-validation.

This parser is deliberately written in a different style (cursor-based
integer parsing, no reuse of the production BitReader/SeiMessage code) so
that agreement between the two implementations gives real confidence in
the supported SEI types.
"""

from dataclasses import dataclass


@dataclass
class RefMessage:
    payload_type: int
    payload_size: int
    raw_payload: bytes


def ref_split_messages(rbsp: bytes) -> list[RefMessage]:
    pos = 0
    out = []
    while pos < len(rbsp) and rbsp[pos] != 0x80:
        ptype = 0
        while rbsp[pos] == 0xFF:
            ptype += 255
            pos += 1
        ptype += rbsp[pos]
        pos += 1
        psize = 0
        while rbsp[pos] == 0xFF:
            psize += 255
            pos += 1
        psize += rbsp[pos]
        pos += 1
        body = rbsp[pos:pos + psize]
        pos += psize
        out.append(RefMessage(ptype, psize, body))
    return out


class _Cursor:
    def __init__(self, data: bytes):
        self.data = data
        self.bit = 0

    def take(self, n: int) -> int:
        value = 0
        for _ in range(n):
            byte = self.data[self.bit >> 3]
            value = (value << 1) | ((byte >> (7 - (self.bit & 7))) & 1)
            self.bit += 1
        return value

    def ue(self) -> int:
        zeros = 0
        while self.take(1) == 0:
            zeros += 1
        return (1 << zeros) - 1 + (self.take(zeros) if zeros else 0)


def ref_recovery_point(body: bytes) -> dict:
    cur = _Cursor(body)
    return {
        "recovery_frame_cnt": cur.ue(),
        "exact_match_flag": bool(cur.take(1)),
        "broken_link_flag": bool(cur.take(1)),
        "changing_slice_group_idc": cur.take(2),
    }


def ref_user_data_unregistered(body: bytes) -> dict:
    return {"uuid": body[:16].hex(), "user_data": body[16:]}


def ref_buffering_period(body: bytes, width: int) -> dict:
    cur = _Cursor(body)
    sps_id = cur.ue()
    values = [cur.take(width) for _ in range(4)]
    return {
        "bp_seq_parameter_set_id": sps_id,
        "nal_delay": values[0],
        "nal_offset": values[1],
        "vcl_delay": values[2],
        "vcl_offset": values[3],
    }


def ref_pic_timing(body: bytes, cpb_len: int, dpb_len: int,
                   time_offset_len: int) -> dict:
    cur = _Cursor(body)
    cpb_delay = cur.take(cpb_len)
    dpb_delay = cur.take(dpb_len)
    pic_struct = cur.ue()
    # one clock timestamp for pic_struct 0
    ct_type = cur.take(2)
    cur.take(1)  # nuit_field_based_flag
    counting_type = cur.take(5)
    full = bool(cur.take(1))
    cur.take(1)  # discontinuity
    cur.take(1)  # cnt_dropped
    n_frames = cur.take(8)
    if full:
        seconds = cur.take(6)
        minutes = cur.take(6)
        hours = cur.take(5)
    else:
        seconds = minutes = hours = None
    time_offset = cur.take(time_offset_len)
    return {
        "cpb_removal_delay": cpb_delay,
        "dpb_output_delay": dpb_delay,
        "pic_struct": pic_struct,
        "ct_type": ct_type,
        "counting_type": counting_type,
        "n_frames": n_frames,
        "seconds": seconds,
        "minutes": minutes,
        "hours": hours,
        "time_offset": time_offset,
    }
