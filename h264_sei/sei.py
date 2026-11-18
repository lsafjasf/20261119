"""SEI message parsing with per-message fault isolation.

Every message parsed from an SEI NAL unit keeps its raw payload bytes,
even when the payload is unknown, truncated or malformed.  A failure in
one message never prevents subsequent messages - or subsequent NAL units
- from being parsed.
"""

from dataclasses import dataclass, field

from .bitreader import BitReader, EndOfBitsError
from .sps import HrdParameters, SequenceParameterSet

SEI_BUFFERING_PERIOD = 0
SEI_PIC_TIMING = 1
SEI_USER_DATA_REGISTERED = 4
SEI_USER_DATA_UNREGISTERED = 5
SEI_RECOVERY_POINT = 6

SEI_MESSAGE_TYPES = {
    SEI_BUFFERING_PERIOD: "buffering_period",
    SEI_PIC_TIMING: "pic_timing",
    SEI_USER_DATA_REGISTERED: "user_data_registered_itu_t_t35",
    SEI_USER_DATA_UNREGISTERED: "user_data_unregistered",
    SEI_RECOVERY_POINT: "recovery_point",
}

# pic_struct values per Table D-1 (num clockTs / clock timestamp pairs).
PIC_STRUCT_NAMES = {
    0: "progressive",
    1: "top_field",
    2: "bottom_field",
    3: "top_field_pic",
    4: "bottom_field_pic",
    5: "top_field_bottom_field",
    6: "bottom_field_top_field",
    7: "frame_doubling",
    8: "frame_tripling",
}

_CLOCK_TS_PER_PIC_STRUCT = {0: 1, 1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3, 7: 2, 8: 3}


def sei_type_name(sei_type: int) -> str:
    return SEI_MESSAGE_TYPES.get(sei_type, f"unknown({sei_type})")


@dataclass
class SeiMessage:
    """One SEI message extracted from an SEI NAL unit."""

    payload_type: int
    payload_size: int
    raw_payload: bytes
    fields: dict = field(default_factory=dict)
    parse_ok: bool = True
    error: str | None = None

    @property
    def type_name(self) -> str:
        return sei_type_name(self.payload_type)

    @property
    def is_unknown(self) -> bool:
        return self.payload_type not in SEI_MESSAGE_TYPES


def _parse_buffering_period(reader: BitReader, hrd: HrdParameters | None) -> dict:
    if hrd is None:
        raise ValueError(
            "buffering period requires HRD parameters from the active SPS"
        )
    fields = {
        "bp_seq_parameter_set_id": reader.read_ue(),
        "cpb_count": hrd.cpb_cnt_minus1 + 1,
        "nal": {"initial_cpb_removal_delay": [], "initial_cpb_removal_delay_offset": []},
        "vcl": {"initial_cpb_removal_delay": [], "initial_cpb_removal_delay_offset": []},
    }
    width = hrd.initial_delay_length
    for _ in range(hrd.cpb_cnt_minus1 + 1):
        fields["nal"]["initial_cpb_removal_delay"].append(reader.read_bits(width))
        fields["nal"]["initial_cpb_removal_delay_offset"].append(reader.read_bits(width))
    for _ in range(hrd.cpb_cnt_minus1 + 1):
        fields["vcl"]["initial_cpb_removal_delay"].append(reader.read_bits(width))
        fields["vcl"]["initial_cpb_removal_delay_offset"].append(reader.read_bits(width))
    return fields


def _parse_clock_timestamp(reader: BitReader, time_offset_length: int) -> dict:
    clock = {
        "ct_type": reader.read_bits(2),
        "nuit_field_based_flag": reader.read_flag(),
        "counting_type": reader.read_bits(5),
        "full_timestamp_flag": reader.read_flag(),
        "discontinuity_flag": reader.read_flag(),
        "cnt_dropped_flag": reader.read_flag(),
        "n_frames": reader.read_bits(8),
    }
    if clock["full_timestamp_flag"]:
        clock.update(
            {
                "seconds_value": reader.read_bits(6),
                "minutes_value": reader.read_bits(6),
                "hours_value": reader.read_bits(5),
            }
        )
    else:
        if reader.read_flag():  # seconds_flag
            clock["seconds_value"] = reader.read_bits(6)
            if reader.read_flag():  # minutes_flag
                clock["minutes_value"] = reader.read_bits(6)
                if reader.read_flag():  # hours_flag
                    clock["hours_value"] = reader.read_bits(5)
    if time_offset_length > 0:
        clock["time_offset"] = reader.read_bits(time_offset_length)
    clock["time_offset_length"] = time_offset_length
    return clock


def _parse_pic_timing(
    reader: BitReader, sps: SequenceParameterSet | None
) -> dict:
    hrd = sps.active_hrd if sps else None
    fields: dict = {}
    if hrd is not None:
        fields["cpb_removal_delay"] = reader.read_bits(hrd.cpb_delay_length)
        fields["dpb_output_delay"] = reader.read_bits(
            hrd.dpb_output_delay_length_minus1 + 1
        )
    elif sps is None:
        raise ValueError("picture timing cannot be parsed without the active SPS")

    if sps is not None and sps.pic_struct_present_flag:
        pic_struct = reader.read_ue()
        fields["pic_struct"] = pic_struct
        fields["pic_struct_name"] = PIC_STRUCT_NAMES.get(
            pic_struct, f"reserved({pic_struct})"
        )
        clocks = []
        clock_count = _CLOCK_TS_PER_PIC_STRUCT.get(pic_struct)
        if clock_count is None:
            raise ValueError(f"reserved pic_struct value {pic_struct}")
        time_offset_length = hrd.time_offset_length if hrd else 0
        for _ in range(clock_count):
            clocks.append(_parse_clock_timestamp(reader, time_offset_length))
        fields["clock_timestamps"] = clocks
    else:
        # Remaining payload bytes are reserved zero bits; keep their length.
        reader.byte_align()
        fields["reserved_remaining_bits"] = reader.remaining_bits()
    return fields


def _parse_user_data_unregistered(reader: BitReader) -> dict:
    raw = reader.data[reader.bit_pos // 8:]
    if len(raw) < 16:
        raise EndOfBitsError(
            f"user_data_unregistered needs 16 uuid bytes, got {len(raw)}"
        )
    uuid = raw[:16]
    user_payload = raw[16:]
    reader.skip_bits(len(raw) * 8)
    return {
        "uuid_iso_iec_11578": uuid.hex(),
        "uuid_formatted": "-".join(
            [uuid[0:4].hex(), uuid[4:6].hex(), uuid[6:8].hex(),
             uuid[8:10].hex(), uuid[10:16].hex()]
        ),
        "user_data": bytes(user_payload),
        "user_data_hex": bytes(user_payload).hex(),
        "user_data_ascii": _safe_ascii(user_payload),
    }


def _parse_recovery_point(reader: BitReader) -> dict:
    fields = {
        "recovery_frame_cnt": reader.read_ue(),
        "exact_match_flag": reader.read_flag(),
        "broken_link_flag": reader.read_flag(),
        "changing_slice_group_idc": reader.read_bits(2),
    }
    reader.byte_align()
    return fields


def _safe_ascii(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def _read_payload_header(rbsp: bytes, offset: int) -> tuple[int, int, int]:
    """Read payloadType / payloadSize with the 0xFF extension convention."""
    payload_type = 0
    while offset < len(rbsp) and rbsp[offset] == 0xFF:
        payload_type += 255
        offset += 1
    if offset >= len(rbsp):
        raise EndOfBitsError("truncated SEI payload type")
    payload_type += rbsp[offset]
    offset += 1

    payload_size = 0
    while offset < len(rbsp) and rbsp[offset] == 0xFF:
        payload_size += 255
        offset += 1
    if offset >= len(rbsp):
        raise EndOfBitsError("truncated SEI payload size")
    payload_size += rbsp[offset]
    offset += 1
    return payload_type, payload_size, offset


def _check_trailing_bits(rbsp: bytes, offset: int) -> str | None:
    """Validate rbsp_trailing_bits after the last message."""
    remaining = rbsp[offset:]
    if not remaining:
        return "missing rbsp_trailing_bits"
    if remaining[0] != 0x80 or any(b != 0 for b in remaining[1:]):
        return f"invalid trailing bytes: {remaining.hex()}"
    return None


def _at_trailing_region(rbsp: bytes, offset: int) -> bool:
    """True when ``offset`` begins a (possibly malformed) trailing-bits tail."""
    return offset >= len(rbsp) or rbsp[offset] == 0x80


def parse_sei_rbsp(
    rbsp: bytes, sps: SequenceParameterSet | None = None
) -> tuple[list[SeiMessage], str | None]:
    """Parse all messages of an SEI NAL unit.

    Returns ``(messages, trailing_bits_warning)``.  A malformed payload is
    reported on its own :class:`SeiMessage` (``parse_ok=False``) while
    parsing continues with the next message.
    """
    messages: list[SeiMessage] = []
    offset = 0
    try:
        while not _at_trailing_region(rbsp, offset):
            payload_type, payload_size, header_end = _read_payload_header(
                rbsp, offset
            )
            payload_start = header_end
            payload_end = payload_start + payload_size
            if payload_end > len(rbsp):
                raw = rbsp[payload_start:]
                # Keep only payload bytes; a trailing 0x80 region belongs to
                # rbsp_trailing_bits rather than to the (truncated) payload.
                if raw and raw[-1] == 0x80:
                    raw = raw[:-1]
                elif len(raw) >= 2 and raw[-2] == 0x80 and raw[-1] == 0x00:
                    raw = raw[:-2]
                messages.append(
                    SeiMessage(
                        payload_type=payload_type,
                        payload_size=payload_size,
                        raw_payload=raw,
                        parse_ok=False,
                        error=(
                            f"declared payload size {payload_size} exceeds "
                            f"remaining bytes {len(raw)}"
                        ),
                    )
                )
                return messages, "stream truncated inside SEI payload"

            raw_payload = rbsp[payload_start:payload_end]
            message = SeiMessage(
                payload_type=payload_type,
                payload_size=payload_size,
                raw_payload=raw_payload,
            )
            _parse_one_payload(message, raw_payload, sps)
            messages.append(message)
            offset = payload_end
    except EndOfBitsError as exc:
        # Structural failure (e.g. header cut off): record it on a synthetic
        # message only if nothing else captured the problem, then stop this
        # NAL without raising - other NAL units stay parseable.
        if not messages or messages[-1].parse_ok:
            messages.append(
                SeiMessage(
                    payload_type=-1,
                    payload_size=0,
                    raw_payload=rbsp[offset:],
                    parse_ok=False,
                    error=f"SEI structure truncated: {exc}",
                )
            )
        return messages, str(exc)

    warning = _check_trailing_bits(rbsp, offset)
    return messages, warning


def _parse_one_payload(
    message: SeiMessage, raw_payload: bytes, sps: SequenceParameterSet | None
) -> None:
    reader = BitReader(raw_payload)
    try:
        if message.payload_type == SEI_BUFFERING_PERIOD:
            hrd = sps.active_hrd if sps else None
            message.fields = _parse_buffering_period(reader, hrd)
        elif message.payload_type == SEI_PIC_TIMING:
            message.fields = _parse_pic_timing(reader, sps)
        elif message.payload_type == SEI_USER_DATA_UNREGISTERED:
            message.fields = _parse_user_data_unregistered(reader)
        elif message.payload_type == SEI_RECOVERY_POINT:
            message.fields = _parse_recovery_point(reader)
        elif message.payload_type == SEI_USER_DATA_REGISTERED:
            message.fields = {"itu_t_t35_payload": raw_payload.hex()}
            reader.skip_bits(len(raw_payload) * 8)
        else:
            # Unknown SEI type: keep raw bytes, no syntax interpretation.
            message.fields = {"raw_hex": raw_payload.hex()}
            reader.skip_bits(len(raw_payload) * 8)
            return
        reader.byte_align()
        if reader.remaining_bits() > 0:
            message.fields["unparsed_remaining_bits"] = reader.remaining_bits()
    except (EndOfBitsError, ValueError) as exc:
        message.parse_ok = False
        message.error = f"{type(exc).__name__}: {exc}"
        message.fields = {"raw_hex": raw_payload.hex()}
