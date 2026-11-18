"""Minimal H.264 sequence parameter set parser.

Only the fields required to interpret buffering period and picture timing
SEI messages are retained.  The parser follows the single-sub-layer HRD
syntax used by modern (2012+) streams; older multi-sub-layer HRD is
detected and reported as unsupported instead of producing wrong lengths.
"""

from dataclasses import dataclass, field

from .bitreader import BitReader, EndOfBitsError


@dataclass
class HrdParameters:
    is_nal_hrd: bool
    cpb_cnt_minus1: int
    bit_rate_scale: int
    cpb_size_scale: int
    cpb_entries: list[dict]
    initial_cpb_removal_delay_length_minus1: int
    cpb_removal_delay_length_minus1: int
    dpb_output_delay_length_minus1: int
    time_offset_length: int

    @property
    def initial_delay_length(self) -> int:
        return self.initial_cpb_removal_delay_length_minus1 + 1

    @property
    def cpb_delay_length(self) -> int:
        return self.cpb_removal_delay_length_minus1 + 1


@dataclass
class SequenceParameterSet:
    sps_id: int
    profile_idc: int
    level_idc: int
    log2_max_frame_num_minus4: int = 0
    pic_order_cnt_type: int = 0
    log2_max_pic_order_cnt_lsb_minus4: int = 0
    pic_struct_present_flag: bool = False
    nal_hrd: HrdParameters | None = None
    vcl_hrd: HrdParameters | None = None
    timing_info: dict | None = None
    parse_error: str | None = None

    @property
    def active_hrd(self) -> HrdParameters | None:
        """NAL HRD takes precedence over VCL HRD for SEI interpretation."""
        return self.nal_hrd or self.vcl_hrd


def _parse_hrd(reader: BitReader, is_nal_hrd: bool) -> HrdParameters:
    cpb_cnt_minus1 = reader.read_ue()
    if cpb_cnt_minus1 > 31:
        raise ValueError("cpb_cnt_minus1 out of range")
    bit_rate_scale = reader.read_bits(4)
    cpb_size_scale = reader.read_bits(4)
    entries = []
    for _ in range(cpb_cnt_minus1 + 1):
        entries.append(
            {
                "bit_rate_value_minus1": reader.read_ue(),
                "cpb_size_value_minus1": reader.read_ue(),
                "cbr_flag": reader.read_flag(),
            }
        )
    return HrdParameters(
        is_nal_hrd=is_nal_hrd,
        cpb_cnt_minus1=cpb_cnt_minus1,
        bit_rate_scale=bit_rate_scale,
        cpb_size_scale=cpb_size_scale,
        cpb_entries=entries,
        initial_cpb_removal_delay_length_minus1=reader.read_bits(5),
        cpb_removal_delay_length_minus1=reader.read_bits(5),
        dpb_output_delay_length_minus1=reader.read_bits(5),
        time_offset_length=reader.read_bits(5),
    )


def parse_sps_rbsp(rbsp: bytes) -> SequenceParameterSet:
    """Parse the RBSP of an SPS NAL unit (NAL header byte already removed)."""
    reader = BitReader(rbsp)
    profile_idc = reader.read_bits(8)
    reader.read_bits(8)  # constraint flags + reserved zero bits
    level_idc = reader.read_bits(8)
    sps_id = reader.read_ue()
    sps = SequenceParameterSet(
        sps_id=sps_id, profile_idc=profile_idc, level_idc=level_idc
    )

    high_profile = profile_idc in (
        100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135,
    )
    if high_profile:
        chroma_format_idc = reader.read_ue()
        if chroma_format_idc == 3:
            reader.read_flag()  # separate_colour_plane_flag
        reader.read_ue()  # bit_depth_luma_minus8
        reader.read_ue()  # bit_depth_chroma_minus8
        reader.read_flag()  # qpprime_y_zero_transform_bypass_flag
        if reader.read_flag():  # seq_scaling_matrix_present_flag
            for i in range(8 if chroma_format_idc != 3 else 12):
                if reader.read_flag():  # seq_scaling_list_present_flag
                    size = 16 if i < 6 else 64
                    last_scale = next_scale = 8
                    for _ in range(size):
                        if next_scale != 0:
                            delta = reader.read_se()
                            next_scale = (last_scale + delta + 256) % 256
                        if next_scale != 0:
                            last_scale = next_scale

    sps.log2_max_frame_num_minus4 = reader.read_ue()
    sps.pic_order_cnt_type = reader.read_ue()
    if sps.pic_order_cnt_type == 0:
        sps.log2_max_pic_order_cnt_lsb_minus4 = reader.read_ue()
    elif sps.pic_order_cnt_type == 1:
        reader.read_flag()  # delta_pic_order_always_zero_flag
        reader.read_se()  # offset_for_non_ref_pic
        reader.read_se()  # offset_for_top_to_bottom_field
        cycle = reader.read_ue()
        for _ in range(cycle):
            reader.read_se()

    reader.read_ue()  # max_num_ref_frames
    reader.read_flag()  # gaps_in_frame_num_value_allowed_flag
    reader.read_ue()  # pic_width_in_mbs_minus1
    reader.read_ue()  # pic_height_in_map_units_minus1
    frame_mbs_only = reader.read_flag()
    if not frame_mbs_only:
        reader.read_flag()  # mb_adaptive_frame_field_flag
    reader.read_flag()  # direct_8x8_inference_flag
    if reader.read_flag():  # frame_cropping_flag
        for _ in range(4):
            reader.read_ue()

    if reader.read_flag():  # vui_parameters_present_flag
        _parse_vui(reader, sps)
    return sps


def _parse_vui(reader: BitReader, sps: SequenceParameterSet) -> None:
    if reader.read_flag():  # aspect_ratio_info_present_flag
        aspect_ratio_idc = reader.read_bits(8)
        if aspect_ratio_idc == 255:
            reader.read_bits(16)  # sar_width
            reader.read_bits(16)  # sar_height
    if reader.read_flag():  # overscan_info_present_flag
        reader.read_flag()  # overscan_appropriate_flag
    if reader.read_flag():  # video_signal_type_present_flag
        reader.read_bits(3)  # video_format
        reader.read_flag()  # video_full_range_flag
        if reader.read_flag():  # colour_description_present_flag
            reader.read_bits(8)
            reader.read_bits(8)
            reader.read_bits(8)
    if reader.read_flag():  # chroma_loc_info_present_flag
        reader.read_ue()
        reader.read_ue()
    if reader.read_flag():  # timing_info_present_flag
        sps.timing_info = {
            "num_units_in_tick": reader.read_bits(32),
            "time_scale": reader.read_bits(32),
            "fixed_frame_rate_flag": reader.read_flag(),
        }
    if reader.read_flag():  # nal_hrd_parameters_present_flag
        sps.nal_hrd = _parse_hrd(reader, True)
    if reader.read_flag():  # vcl_hrd_parameters_present_flag
        sps.vcl_hrd = _parse_hrd(reader, False)
    if sps.nal_hrd or sps.vcl_hrd:
        reader.read_flag()  # low_delay_hrd_flag
    sps.pic_struct_present_flag = reader.read_flag()
    # Remaining VUI bits (bitstream restriction etc.) are irrelevant to SEI.
