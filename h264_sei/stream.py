"""NAL unit extraction, SPS tracking and access unit grouping."""

from dataclasses import dataclass, field

from .rbsp import annex_b_ranges, remove_emulation_prevention
from .sei import SeiMessage, parse_sei_rbsp
from .sps import SequenceParameterSet, parse_sps_rbsp

NAL_SLICE = 1
NAL_IDR = 5
NAL_SEI = 6
NAL_SPS = 7
NAL_PPS = 8


@dataclass
class NalUnit:
    index: int
    nal_type: int
    nal_ref_idc: int
    raw_ebsp: bytes
    rbsp: bytes
    stream_offset: int
    messages: list[SeiMessage] = field(default_factory=list)
    sei_warning: str | None = None
    parse_error: str | None = None
    sps: SequenceParameterSet | None = None

    @property
    def is_vcl(self) -> bool:
        return self.nal_type in (1, 2, 3, 4, 5)

    @property
    def type_name(self) -> str:
        names = {
            1: "slice_non_idr", 2: "slice_data_partition_a",
            3: "slice_data_partition_b", 4: "slice_data_partition_c",
            5: "slice_idr", 6: "sei", 7: "sps", 8: "pps",
            9: "aud", 10: "end_of_seq", 11: "end_of_stream",
            12: "filler", 13: "sps_extension", 14: "prefix_nal_unit",
            15: "subset_sps",
        }
        return names.get(self.nal_type, f"type_{self.nal_type}")


@dataclass
class AccessUnit:
    index: int
    nals: list[NalUnit] = field(default_factory=list)
    first_mb_in_slice: int | None = None
    frame_num: int | None = None
    idr_pic_id: int | None = None
    is_idr: bool = False

    @property
    def sei_nals(self) -> list[NalUnit]:
        return [nal for nal in self.nals if nal.nal_type == NAL_SEI]

    @property
    def all_sei_messages(self) -> list[tuple[NalUnit, SeiMessage]]:
        return [(nal, msg) for nal in self.sei_nals for msg in nal.messages]


def extract_nal_units(stream: bytes) -> list[NalUnit]:
    """Split the stream and parse SPS/SEI NAL units with fault isolation."""
    data = bytes(stream)
    ranges = annex_b_ranges(data)
    sps_map: dict[int, SequenceParameterSet] = {}
    result: list[NalUnit] = []

    for index, (stream_offset, payload_start, payload_end) in enumerate(ranges):
        body = data[payload_start:payload_end]
        if index + 1 < len(ranges):
            body = body.rstrip(b"\x00")
        if not body:
            continue
        header = body[0]
        nal_type = header & 0x1F
        nal_ref_idc = (header >> 5) & 0x03
        try:
            rbsp = remove_emulation_prevention(body[1:])
        except Exception as exc:  # defensive: never break the stream walk
            rbsp = body[1:]
        nal = NalUnit(
            index=index,
            nal_type=nal_type,
            nal_ref_idc=nal_ref_idc,
            raw_ebsp=body,
            rbsp=rbsp,
            stream_offset=stream_offset,
        )

        if nal_type == NAL_SPS:
            try:
                nal.sps = parse_sps_rbsp(rbsp)
                sps_map[nal.sps.sps_id] = nal.sps
            except Exception as exc:
                nal.parse_error = f"SPS parse error: {type(exc).__name__}: {exc}"
        elif nal_type == NAL_SEI:
            active_sps = _latest_sps(sps_map)
            try:
                nal.messages, nal.sei_warning = parse_sei_rbsp(rbsp, active_sps)
            except Exception as exc:  # last-resort guard
                nal.parse_error = f"SEI parse error: {type(exc).__name__}: {exc}"

        result.append(nal)
    return result


def _latest_sps(sps_map: dict[int, SequenceParameterSet]) -> SequenceParameterSet | None:
    if not sps_map:
        return None
    return sps_map[max(sps_map)]


def extract_access_units(stream: bytes) -> list[AccessUnit]:
    """Group NAL units into access units.

    A new access unit starts at every VCL NAL; non-VCL NAL units (AUD,
    SPS/PPS, SEI) are attached to the AU whose VCL NAL follows them.
    Trailing non-VCL NALs form their own AU.
    """
    nals = extract_nal_units(stream)
    access_units: list[AccessUnit] = []
    current = AccessUnit(index=0)
    access_units.append(current)
    vcl_seen = False
    # Non-VCL NALs seen after the previous VCL NAL; they belong to the next
    # AU once its VCL NAL arrives.
    pending_non_vcl: list[NalUnit] = []

    for nal in nals:
        if nal.is_vcl:
            first_mb = _peek_first_mb(nal)
            if vcl_seen and first_mb == 0:
                current = AccessUnit(index=len(access_units))
                access_units.append(current)
                current.nals.extend(pending_non_vcl)
                pending_non_vcl = []
            vcl_seen = True
            current.nals.append(nal)
            _annotate_vcl(nal, current)
        elif vcl_seen:
            pending_non_vcl.append(nal)
        else:
            # Before the first VCL NAL everything joins the opening AU.
            current.nals.append(nal)

    if pending_non_vcl:
        tail = AccessUnit(index=len(access_units))
        tail.nals.extend(pending_non_vcl)
        access_units.append(tail)

    return [au for au in access_units if au.nals]


def _peek_first_mb(nal: NalUnit) -> int | None:
    try:
        from .bitreader import BitReader

        return BitReader(nal.rbsp).read_ue()
    except Exception:
        return None


def _annotate_vcl(nal: NalUnit, au: AccessUnit) -> None:
    au.is_idr = nal.nal_type == NAL_IDR
    try:
        from .bitreader import BitReader

        reader = BitReader(nal.rbsp)
        au.first_mb_in_slice = reader.read_ue()
        if nal.nal_type == NAL_IDR:
            au.idr_pic_id = reader.read_ue()
    except Exception:
        pass
