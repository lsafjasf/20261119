"""Synthetic Annex B H.264 stream builder shared by the test-suite."""

from .bitwriter import BitWriter, annexb_nal


def build_hrd(writer: BitWriter, *, cpb_cnt_minus1=0, initial_len=8,
              cpb_len=10, dpb_len=12, time_offset_len=9) -> None:
    writer.write_ue(cpb_cnt_minus1)
    writer.write_bits(0, 4)  # bit_rate_scale
    writer.write_bits(0, 4)  # cpb_size_scale
    for _ in range(cpb_cnt_minus1 + 1):
        writer.write_ue(0)  # bit_rate_value_minus1
        writer.write_ue(0)  # cpb_size_value_minus1
        writer.write_flag(False)  # cbr_flag
    writer.write_bits(initial_len - 1, 5)
    writer.write_bits(cpb_len - 1, 5)
    writer.write_bits(dpb_len - 1, 5)
    writer.write_bits(time_offset_len, 5)


def build_sps_rbsp(*, with_hrd=True, pic_struct_present=True,
                   cpb_cnt_minus1=0) -> bytes:
    w = BitWriter()
    w.write_bits(66, 8)  # profile_idc (baseline)
    w.write_bits(0, 8)   # constraint flags
    w.write_bits(30, 8)  # level_idc
    w.write_ue(0)        # sps id
    w.write_ue(0)        # log2_max_frame_num_minus4
    w.write_ue(0)        # pic_order_cnt_type
    w.write_ue(0)        # log2_max_pic_order_cnt_lsb_minus4
    w.write_ue(1)        # max_num_ref_frames
    w.write_flag(False)  # gaps_in_frame_num_value_allowed_flag
    w.write_ue(10)       # pic_width_in_mbs_minus1 (176 px)
    w.write_ue(8)        # pic_height_in_map_units_minus1 (144 px)
    w.write_flag(True)   # frame_mbs_only_flag
    w.write_flag(False)  # direct_8x8_inference_flag
    w.write_flag(False)  # frame_cropping_flag
    w.write_flag(True)   # vui_parameters_present_flag
    # VUI
    w.write_flag(False)  # aspect_ratio
    w.write_flag(False)  # overscan
    w.write_flag(False)  # video_signal_type
    w.write_flag(False)  # chroma_loc
    w.write_flag(True)   # timing_info_present_flag
    w.write_bits(1000, 32)  # num_units_in_tick
    w.write_bits(50000, 32)  # time_scale
    w.write_flag(True)   # fixed_frame_rate_flag
    if with_hrd:
        w.write_flag(True)  # nal_hrd_parameters_present_flag
        build_hrd(w, cpb_cnt_minus1=cpb_cnt_minus1)
        w.write_flag(False)  # vcl_hrd_parameters_present_flag
        w.write_flag(False)  # low_delay_hrd_flag
    w.write_flag(pic_struct_present)  # pic_struct_present_flag
    w.write_flag(False)  # bitstream_restriction_flag
    w.rbsp_trailing_bits()
    return w.to_bytes()


def build_sps_nal(**kwargs) -> bytes:
    return annexb_nal(0x67, build_sps_rbsp(**kwargs))


def build_pps_nal() -> bytes:
    w = BitWriter()
    w.write_ue(0)  # pps id
    w.write_ue(0)  # sps id
    w.write_flag(False)  # entropy_coding_mode_flag
    w.write_flag(False)  # bottom_field_pic_order_in_frame_present_flag
    w.write_ue(0)  # num_slice_groups_minus1
    w.write_ue(0)  # num_ref_idx_l0_default_active_minus1
    w.write_ue(0)  # num_ref_idx_l1_default_active_minus1
    w.write_flag(False)  # weighted_pred_flag
    w.write_bits(0, 2)  # weighted_bipred_idc
    w.write_se(0)  # pic_init_qp_minus26
    w.write_se(0)  # pic_init_qs_minus26
    w.write_se(0)  # chroma_qp_index_offset
    w.write_flag(True)  # deblocking_filter_control_present_flag
    w.write_flag(False)  # constrained_intra_pred_flag
    w.write_flag(False)  # redundant_pic_cnt_present_flag
    w.rbsp_trailing_bits()
    return annexb_nal(0x68, w.to_bytes())


def build_idr_nal() -> bytes:
    # A syntactically minimal IDR slice header; slice data is not needed
    # for SEI-level testing.
    w = BitWriter()
    w.write_ue(0)  # first_mb_in_slice
    w.write_ue(7)  # slice_type I
    w.write_ue(0)  # pic_parameter_set_id
    w.write_bits(0, 4)  # frame_num (log2_max_frame_num = 4)
    w.write_ue(0)  # idr_pic_id
    w.write_bits(0, 4)  # pic_order_cnt_lsb
    w.rbsp_trailing_bits()
    return annexb_nal(0x65, w.to_bytes())


def build_sei_nal(messages: list[bytes], nal_ref_idc: int = 0) -> bytes:
    """Wrap one or more raw SEI payloads into a complete SEI NAL unit."""
    rbsp = bytearray()
    for payload in messages:
        rbsp.extend(payload)
    rbsp.append(0x80)  # rbsp_trailing_bits
    header = (nal_ref_idc << 5) | 6
    return annexb_nal(header, bytes(rbsp))


def sei_payload(payload_type: int, payload_body: bytes) -> bytes:
    header = bytearray()
    type_left = payload_type
    while type_left >= 255:
        header.append(0xFF)
        type_left -= 255
    header.append(type_left)
    size_left = len(payload_body)
    while size_left >= 255:
        header.append(0xFF)
        size_left -= 255
    header.append(size_left)
    return bytes(header) + payload_body


def buffering_period_payload() -> bytes:
    w = BitWriter()
    w.write_ue(0)  # bp_seq_parameter_set_id
    w.write_bits(0x11, 8)  # initial_cpb_removal_delay (len = 8)
    w.write_bits(0x22, 8)  # initial_cpb_removal_delay_offset
    w.write_bits(0x33, 8)  # VCL delay
    w.write_bits(0x44, 8)  # VCL offset
    w.rbsp_trailing_bits()
    return w.to_bytes()


def pic_timing_payload(*, full_timestamp=True, time_offset=0xAA,
                       pic_struct=0) -> bytes:
    w = BitWriter()
    w.write_bits(0x05, 10)  # cpb_removal_delay (len = 10)
    w.write_bits(0x07, 12)  # dpb_output_delay (len = 12)
    w.write_ue(pic_struct)
    # one clock timestamp for pic_struct 0
    w.write_bits(1, 2)  # ct_type
    w.write_flag(False)  # nuit_field_based_flag
    w.write_bits(0, 5)  # counting_type
    w.write_flag(full_timestamp)
    w.write_flag(False)  # discontinuity
    w.write_flag(False)  # cnt_dropped
    w.write_bits(24, 8)  # n_frames
    if full_timestamp:
        w.write_bits(30, 6)  # seconds
        w.write_bits(15, 6)  # minutes
        w.write_bits(9, 5)   # hours
    w.write_bits(time_offset, 9)  # time_offset_length = 9
    w.rbsp_trailing_bits()
    return w.to_bytes()


def user_data_unregistered_payload(uuid: bytes, user_data: bytes) -> bytes:
    assert len(uuid) == 16
    return uuid + user_data


def recovery_point_payload() -> bytes:
    w = BitWriter()
    w.write_ue(3)   # recovery_frame_cnt
    w.write_flag(True)  # exact_match_flag
    w.write_flag(False)  # broken_link_flag
    w.write_bits(2, 2)  # changing_slice_group_idc
    w.rbsp_trailing_bits()
    return w.to_bytes()
