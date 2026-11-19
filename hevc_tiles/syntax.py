"""HEVC parameter-set and slice-header syntax needed for tile extraction."""

from __future__ import annotations

from .bitstream import BitReader
from .models import IDR_TYPES, IRAP_TYPES, ParsedSliceHeader, Pps, ShortTermRps, Sps


def parse_profile_tier_level(reader: BitReader, max_sub_layers_minus1: int) -> int:
    profile_space = reader.read_bits(2)
    tier_flag = reader.read_bit()
    profile_idc = reader.read_bits(5)
    compatibility_flags = [reader.read_bit() for _ in range(32)]
    if profile_idc == 0:
        for index in range(1, 32):
            if compatibility_flags[index]:
                profile_idc = index
                break
    # Four source-format flags plus the profile-dependent 44-bit constraint
    # block (the latter has a constant size for all profiles).
    reader.skip(4 + 44)
    reader.skip(8)  # general_level_idc
    _ = profile_space, tier_flag

    profile_present = [reader.read_bit() for _ in range(max_sub_layers_minus1)]
    level_present = [reader.read_bit() for _ in range(max_sub_layers_minus1)]
    if max_sub_layers_minus1:
        reader.skip(2 * (8 - max_sub_layers_minus1))
    for index in range(max_sub_layers_minus1):
        if profile_present[index]:
            reader.skip(2 + 1 + 5 + 32 + 4 + 44)
        if level_present[index]:
            reader.skip(8)
    return profile_idc


def parse_short_term_rps(
    reader: BitReader, sps: Sps, *, in_slice_header: bool
) -> ShortTermRps:
    predicted = False
    if in_slice_header and sps.short_term_rps:
        predicted = bool(reader.read_bit())

    if predicted:
        delta_idx_minus1 = reader.read_ue()
        delta_idx = delta_idx_minus1 + 1
        if delta_idx > len(sps.short_term_rps):
            raise ValueError("short-term RPS delta_idx exceeds SPS RPS count")
        reference = sps.short_term_rps[-delta_idx]
        reader.read_bit()  # delta_rps_sign
        reader.read_ue()  # abs_delta_rps_minus1
        num_delta_pocs = len(reference.delta_pocs)
        kept_deltas: list[int] = []
        used_by_curr: list[bool] = []
        for _ in range(num_delta_pocs + 1):
            used = reader.read_bit()
            use_delta = 0
            if not used:
                use_delta = reader.read_bit()  # use_delta_flag
            if used or use_delta:
                kept_deltas.append(0)
                used_by_curr.append(bool(used))
        return ShortTermRps(0, 0, kept_deltas, used_by_curr)

    num_negative = reader.read_ue()
    num_positive = reader.read_ue()
    if num_negative > 31 or num_positive > 31:
        raise ValueError("short-term RPS has too many pictures")
    negative: list[int] = []
    positive: list[int] = []
    used: list[bool] = []
    prev = 0
    for _ in range(num_negative):
        prev -= reader.read_ue() + 1
        negative.append(prev)
        used.append(bool(reader.read_bit()))
    prev = 0
    for _ in range(num_positive):
        prev += reader.read_ue() + 1
        positive.append(prev)
        used.append(bool(reader.read_bit()))
    return ShortTermRps(num_negative, num_positive, negative + positive, used)


def _skip_scaling_list(reader: BitReader) -> None:
    for size_id in range(4):
        for matrix_id in range(0, 6, 3 if size_id == 3 else 1):
            if reader.read_bit():  # scaling_list_pred_mode_flag
                if size_id > 1:
                    reader.read_se()  # scaling_list_dc_coef_minus8
                for _ in range(min(64, 1 << (4 + (size_id << 1)))):
                    reader.read_se()
            else:
                reader.read_ue()  # scaling_list_pred_matrix_id_delta
            _ = matrix_id


def _skip_hrd_parameters(reader: BitReader, max_sub_layers_minus1: int) -> None:
    nal_hrd = reader.read_bit()
    vcl_hrd = reader.read_bit()
    if not (nal_hrd or vcl_hrd):
        return
    sub_pic = reader.read_bit()
    if sub_pic:
        reader.skip(8 + 5 + 1 + 5)
    reader.skip(4 + 4)
    if sub_pic:
        reader.skip(4)
    reader.skip(5 + 5 + 5)
    for _ in range(max_sub_layers_minus1 + 1):
        fixed_general = reader.read_bit()
        fixed_within = 0
        if not fixed_general:
            fixed_within = reader.read_bit()
        if not (fixed_general or fixed_within):
            reader.read_bit()  # low_delay_hrd_flag
        else:
            reader.read_ue()  # elemental_duration_in_tc_minus1
        cpb_cnt_minus1 = reader.read_ue()
        if cpb_cnt_minus1 > 31:
            raise ValueError("HRD cpb_cnt_minus1 is out of range")
        for _ in range(cpb_cnt_minus1 + 1):
            reader.read_ue()
            reader.read_ue()
            if sub_pic:
                reader.read_ue()
                reader.read_ue()
            reader.read_bit()


def _skip_vui(reader: BitReader, max_sub_layers_minus1: int) -> None:
    if reader.read_bit():  # aspect_ratio_info_present_flag
        if reader.read_bits(8) == 255:  # aspect_ratio_idc
            reader.skip(16 + 16)
    if reader.read_bit():  # overscan_info_present_flag
        reader.read_bit()
    if reader.read_bit():  # video_signal_type_present_flag
        reader.skip(3 + 1)
        if reader.read_bit():  # colour_description_present_flag
            reader.skip(8 + 8 + 8)
    if reader.read_bit():  # chroma_loc_info_present_flag
        reader.read_ue()
        reader.read_ue()
    reader.read_bit()  # neutral_chroma_indication_flag
    reader.read_bit()  # field_seq_flag
    reader.read_bit()  # frame_field_info_present_flag
    if reader.read_bit():  # default_display_window_flag
        for _ in range(4):
            reader.read_ue()
    if reader.read_bit():  # vui_timing_info_present_flag
        reader.skip(32 + 32)
        if reader.read_bit():  # vui_poc_proportional_to_timing_flag
            reader.read_ue()
        if reader.read_bit():  # vui_hrd_parameters_present_flag
            _skip_hrd_parameters(reader, max_sub_layers_minus1)
    if reader.read_bit():  # bitstream_restriction_flag
        reader.skip(3)
        reader.read_ue()
        reader.read_ue()
        reader.read_ue()
        reader.read_ue()
        reader.read_ue()


def parse_sps(rbsp: bytes) -> Sps:
    reader = BitReader(rbsp, name="SPS")
    reader.skip(4)  # sps_video_parameter_set_id
    max_sub_layers_minus1 = reader.read_bits(3)
    reader.read_bit()  # sps_temporal_id_nesting_flag
    profile_idc = parse_profile_tier_level(reader, max_sub_layers_minus1)
    sps_id = reader.read_ue()
    chroma_format_idc = reader.read_ue()
    if chroma_format_idc > 3:
        raise ValueError(f"invalid chroma_format_idc {chroma_format_idc}")
    separate_colour_plane = reader.read_bit() if chroma_format_idc == 3 else 0
    width = reader.read_ue()
    height = reader.read_ue()
    if reader.read_bit():  # conformance_window_flag
        for _ in range(4):
            reader.read_ue()
    bit_depth_luma = reader.read_ue() + 8
    bit_depth_chroma = reader.read_ue() + 8
    log2_max_poc_lsb = reader.read_ue() + 4
    ordering_info_present = reader.read_bit()
    start_layer = 0 if ordering_info_present else max_sub_layers_minus1
    for _ in range(start_layer, max_sub_layers_minus1 + 1):
        reader.read_ue()
        reader.read_ue()
        reader.read_ue()
    log2_min_cb = reader.read_ue() + 3
    log2_diff_cb = reader.read_ue()
    reader.read_ue()  # log2_min_luma_transform_block_size_minus2
    reader.read_ue()  # log2_diff_max_min_luma_transform_block_size
    reader.read_ue()  # max_transform_hierarchy_depth_inter
    reader.read_ue()  # max_transform_hierarchy_depth_intra
    if reader.read_bit():  # scaling_list_enabled_flag
        if reader.read_bit():  # sps_scaling_list_data_present_flag
            _skip_scaling_list(reader)
    reader.read_bit()  # amp_enabled_flag
    sao_enabled = bool(reader.read_bit())
    if reader.read_bit():  # pcm_enabled_flag
        reader.skip(4 + 4)
        reader.read_ue()
        reader.read_ue()
        reader.read_bit()

    short_term_rps: list[ShortTermRps] = []
    placeholder = Sps(
        sps_id=sps_id,
        chroma_format_idc=chroma_format_idc,
        short_term_rps=short_term_rps,
    )
    num_st_rps = reader.read_ue()
    if num_st_rps > 64:
        raise ValueError("too many short-term reference picture sets")
    for index in range(num_st_rps):
        # SPS RPS 0 has inter_ref_pic_set_prediction_flag inferred as zero.
        if index:
            predicted = reader.read_bit()
            if predicted:
                raise ValueError("predicted SPS short-term RPS is not supported")
        short_term_rps.append(
            parse_short_term_rps(reader, placeholder, in_slice_header=False)
        )

    long_term_present = bool(reader.read_bit())
    num_long_term = 0
    long_term_used_sps: list[bool] = []
    if long_term_present:
        num_long_term = reader.read_ue()
        for _ in range(num_long_term):
            reader.skip(log2_max_poc_lsb + 1)
            long_term_used_sps.append(bool(reader.read_bit()))
    temporal_mvp = bool(reader.read_bit())
    reader.read_bit()  # strong_intra_smoothing_enabled_flag
    if reader.read_bit():  # vui_parameters_present_flag
        _skip_vui(reader, max_sub_layers_minus1)

    mv_resolution_idc = 0
    curr_pic_ref = False
    if reader.read_bit():  # sps_extension_present_flag
        range_ext = reader.read_bit()
        multilayer_ext = reader.read_bit()
        ext_3d = reader.read_bit()
        scc_ext = reader.read_bit()
        reader.skip(4)
        if range_ext:
            reader.skip(10)
        if multilayer_ext:
            reader.read_bit()
        if ext_3d:
            _skip_3d_sps_extension(reader)
        if scc_ext:
            curr_pic_ref = bool(reader.read_bit())
            palette = reader.read_bit()
            if palette:
                reader.read_ue()
                reader.read_ue()
                if reader.read_bit():
                    count = reader.read_ue() + 1
                    components = 1 if chroma_format_idc == 0 else 3
                    for component in range(components):
                        depth = bit_depth_luma if component == 0 else bit_depth_chroma
                        reader.skip(count * depth)
            mv_resolution_idc = reader.read_bits(2)
            reader.read_bit()  # intra_boundary_filtering_disabled_flag

    return Sps(
        sps_id=sps_id,
        profile_idc=profile_idc,
        max_sub_layers_minus1=max_sub_layers_minus1,
        chroma_format_idc=chroma_format_idc,
        separate_colour_plane_flag=separate_colour_plane,
        width=width,
        height=height,
        bit_depth_luma=bit_depth_luma,
        bit_depth_chroma=bit_depth_chroma,
        log2_max_poc_lsb=log2_max_poc_lsb,
        log2_min_cb_size=log2_min_cb,
        log2_diff_max_min_cb_size=log2_diff_cb,
        ctb_size=1 << (log2_min_cb + log2_diff_cb),
        sao_enabled=sao_enabled,
        long_term_ref_pics_present=long_term_present,
        num_long_term_ref_pics_sps=num_long_term,
        long_term_used_by_curr_sps=long_term_used_sps,
        temporal_mvp_enabled=temporal_mvp,
        short_term_rps=short_term_rps,
        motion_vector_resolution_control_idc=mv_resolution_idc,
        sps_curr_pic_ref_enabled=curr_pic_ref,
    )


def _skip_3d_sps_extension(reader: BitReader) -> None:
    for index in range(2):
        reader.read_bit()
        reader.read_bit()
        if index == 0:
            reader.read_ue()
            reader.skip(4)
        else:
            reader.read_ue()
            reader.skip(5)


def parse_pps(rbsp: bytes, sps: Sps) -> Pps:
    reader = BitReader(rbsp, name="PPS")
    pps_id = reader.read_ue()
    sps_id = reader.read_ue()
    dependent_slice_segments = bool(reader.read_bit())
    output_flag_present = bool(reader.read_bit())
    extra_slice_bits = reader.read_bits(3)
    reader.read_bit()  # sign_data_hiding_enabled
    cabac_init_present = bool(reader.read_bit())
    num_l0 = reader.read_ue() + 1
    num_l1 = reader.read_ue() + 1
    init_qp = reader.read_se()
    reader.read_bit()  # constrained_intra_pred_flag
    transform_skip_enabled = bool(reader.read_bit())
    if reader.read_bit():  # cu_qp_delta_enabled_flag
        reader.read_ue()
    reader.read_se()  # pps_cb_qp_offset
    reader.read_se()  # pps_cr_qp_offset
    chroma_qp_offsets_present = bool(reader.read_bit())
    weighted_pred = bool(reader.read_bit())
    weighted_bipred = bool(reader.read_bit())
    reader.read_bit()  # transquant_bypass_enabled_flag
    tiles_enabled = bool(reader.read_bit())
    entropy_sync = bool(reader.read_bit())

    num_columns = num_rows = 1
    uniform_spacing = True
    column_width = [sps.ctb_width]
    row_height = [sps.ctb_height]
    loop_filter_across_tiles = True
    loop_filter_flag_bit = None
    if tiles_enabled:
        num_columns = reader.read_ue() + 1
        num_rows = reader.read_ue() + 1
        if num_columns > sps.ctb_width or num_rows > sps.ctb_height:
            raise ValueError("tile grid exceeds the coded picture in CTBs")
        uniform_spacing = bool(reader.read_bit())
        if uniform_spacing:
            column_width = [
                (i + 1) * sps.ctb_width // num_columns
                - i * sps.ctb_width // num_columns
                for i in range(num_columns)
            ]
            row_height = [
                (i + 1) * sps.ctb_height // num_rows
                - i * sps.ctb_height // num_rows
                for i in range(num_rows)
            ]
        else:
            column_width = [reader.read_ue() + 1 for _ in range(num_columns - 1)]
            column_width.append(sps.ctb_width - sum(column_width))
            row_height = [reader.read_ue() + 1 for _ in range(num_rows - 1)]
            row_height.append(sps.ctb_height - sum(row_height))
        loop_filter_flag_bit = reader.bitpos
        loop_filter_across_tiles = bool(reader.read_bit())

    loop_filter_across_slices = bool(reader.read_bit())
    deblock_present = bool(reader.read_bit())
    deblock_override = False
    deblock_disabled = False
    beta = tc = 0
    if deblock_present:
        deblock_override = bool(reader.read_bit())
        deblock_disabled = bool(reader.read_bit())
        if not deblock_disabled:
            beta = reader.read_se()
            tc = reader.read_se()

    if reader.read_bit():  # pps_scaling_list_data_present_flag
        _skip_scaling_list(reader)
    lists_modification = bool(reader.read_bit())
    reader.read_ue()  # log2_parallel_merge_level_minus2
    slice_header_extension = bool(reader.read_bit())

    chroma_qp_list_enabled = False
    act_qp_offsets_present = False
    curr_pic_ref = False
    if reader.read_bit():  # pps_extension_present_flag
        range_ext = reader.read_bit()
        multilayer_ext = reader.read_bit()
        ext_3d = reader.read_bit()
        scc_ext = reader.read_bit()
        reader.skip(4)
        if range_ext:
            if transform_skip_enabled:
                reader.read_ue()
            reader.read_bit()  # cross_component_prediction_enabled_flag
            chroma_qp_list_enabled = bool(reader.read_bit())
            if chroma_qp_list_enabled:
                reader.read_ue()
                list_len = reader.read_ue() + 1
                for _ in range(list_len):
                    reader.read_se()
                    reader.read_se()
            reader.read_ue()
            reader.read_ue()
        if multilayer_ext or ext_3d:
            raise ValueError("multilayer/3D PPS extensions are not supported")
        if scc_ext:
            curr_pic_ref = bool(reader.read_bit())
            act_enabled = bool(reader.read_bit())
            if act_enabled:
                act_qp_offsets_present = bool(reader.read_bit())
                reader.read_se()
                reader.read_se()
                reader.read_se()
            if reader.read_bit():  # pps_palette_predictor_initializers_present_flag
                count = reader.read_ue()
                if count:
                    monochrome = bool(reader.read_bit())
                    luma_depth = reader.read_ue() + 8
                    chroma_depth = luma_depth
                    if not monochrome:
                        chroma_depth = reader.read_ue() + 8
                    components = 1 if monochrome else 3
                    for component in range(components):
                        depth = luma_depth if component == 0 else chroma_depth
                        reader.skip(count * depth)

    return Pps(
        pps_id=pps_id,
        sps_id=sps_id,
        dependent_slice_segments_enabled=dependent_slice_segments,
        output_flag_present=output_flag_present,
        num_extra_slice_header_bits=extra_slice_bits,
        cabac_init_present=cabac_init_present,
        num_ref_idx_l0_default=num_l0,
        num_ref_idx_l1_default=num_l1,
        init_qp_minus26=init_qp,
        weighted_pred=weighted_pred,
        weighted_bipred=weighted_bipred,
        slice_chroma_qp_offsets_present=chroma_qp_offsets_present,
        tiles_enabled=tiles_enabled,
        entropy_coding_sync_enabled=entropy_sync,
        num_tile_columns=num_columns,
        num_tile_rows=num_rows,
        uniform_spacing=uniform_spacing,
        column_width=column_width,
        row_height=row_height,
        loop_filter_across_tiles_enabled=loop_filter_across_tiles,
        loop_filter_across_tiles_flag_bit=loop_filter_flag_bit,
        loop_filter_across_slices_enabled=loop_filter_across_slices,
        deblocking_filter_control_present=deblock_present,
        deblocking_filter_override_enabled=deblock_override,
        deblocking_filter_disabled=deblock_disabled,
        beta_offset_div2=beta,
        tc_offset_div2=tc,
        lists_modification_present=lists_modification,
        slice_header_extension_present=slice_header_extension,
        chroma_qp_offset_list_enabled=chroma_qp_list_enabled,
        pps_slice_act_qp_offsets_present=act_qp_offsets_present,
        pps_curr_pic_ref_enabled=curr_pic_ref,
    )


def _ceil_log2(value: int) -> int:
    return max(0, (value - 1).bit_length())


def _skip_pred_weight_table(
    reader: BitReader, sps: Sps, slice_type: int, num_l0: int, num_l1: int
) -> None:
    reader.read_ue()  # luma_log2_weight_denom
    if sps.chroma_format_idc:
        reader.read_se()  # delta_chroma_log2_weight_denom
    luma_flags = [[reader.read_bit() for _ in range(count)] for count in (num_l0, num_l1)]
    chroma_flags: list[list[int]] = [[], []]
    if sps.chroma_format_idc:
        chroma_flags[0] = [reader.read_bit() for _ in range(num_l0)]
        if slice_type == 0:
            chroma_flags[1] = [reader.read_bit() for _ in range(num_l1)]
    for list_index in range(2 if slice_type == 0 else 1):
        count = num_l0 if list_index == 0 else num_l1
        for index in range(count):
            if luma_flags[list_index][index]:
                reader.read_se()
                reader.read_se()
            if chroma_flags[list_index][index]:
                for _ in range(4):
                    reader.read_se()


def parse_slice_header(
    rbsp: bytes,
    nal_type: int,
    pps: Pps,
    sps: Sps,
) -> ParsedSliceHeader:
    reader = BitReader(rbsp, name="slice header")
    first_in_pic = bool(reader.read_bit())
    if nal_type in IRAP_TYPES:
        reader.read_bit()  # no_output_of_prior_pics_flag
    pps_id = reader.read_ue()
    prefix_end_bit = reader.bitpos

    dependent = False
    address = 0
    has_address = False
    if not first_in_pic:
        has_address = True
        if pps.dependent_slice_segments_enabled:
            dependent = bool(reader.read_bit())
        address = reader.read_bits(
            _ceil_log2(sps.ctb_width * sps.ctb_height)
        )
    body_start_bit = reader.bitpos

    slice_type = 2
    poc_lsb: int | None = None
    if not dependent:
        for _ in range(pps.num_extra_slice_header_bits):
            reader.read_bit()
        slice_type = reader.read_ue()
        if slice_type not in (0, 1, 2):
            raise ValueError(f"invalid slice_type {slice_type}")
        if pps.output_flag_present:
            reader.read_bit()
        if sps.separate_colour_plane_flag:
            reader.skip(2)

        temporal_mvp = False
        num_long_term_used = 0
        if nal_type not in IDR_TYPES:
            poc_lsb = reader.read_bits(sps.log2_max_poc_lsb)
            rps_from_sps = bool(reader.read_bit())
            if rps_from_sps:
                if not sps.short_term_rps:
                    raise ValueError("slice references an empty SPS RPS list")
                index_bits = _ceil_log2(len(sps.short_term_rps))
                rps_index = reader.read_bits(index_bits) if index_bits else 0
                if rps_index >= len(sps.short_term_rps):
                    raise ValueError("short_term_ref_pic_set_idx is out of range")
                current_rps = sps.short_term_rps[rps_index]
            else:
                current_rps = parse_short_term_rps(
                    reader, sps, in_slice_header=True
                )
            num_long_term_used = _skip_long_term_ref_pics(reader, sps)
            if sps.temporal_mvp_enabled:
                temporal_mvp = bool(reader.read_bit())
        else:
            current_rps = ShortTermRps(0, 0)

        sao_luma = False
        sao_chroma = False
        if sps.sao_enabled:
            sao_luma = bool(reader.read_bit())
            if sps.chroma_format_idc:
                sao_chroma = bool(reader.read_bit())

        num_l0 = pps.num_ref_idx_l0_default
        num_l1 = pps.num_ref_idx_l1_default
        if slice_type in (0, 1):
            if reader.read_bit():  # num_ref_idx_active_override_flag
                num_l0 = reader.read_ue() + 1
                if slice_type == 0:
                    num_l1 = reader.read_ue() + 1
            if pps.lists_modification_present:
                nb_refs = sum(current_rps.used_by_curr) + num_long_term_used
                if nb_refs > 1:
                    if reader.read_bit():
                        for _ in range(num_l0):
                            reader.skip(_ceil_log2(nb_refs))
                    if slice_type == 0 and reader.read_bit():
                        for _ in range(num_l1):
                            reader.skip(_ceil_log2(nb_refs))
            if slice_type == 0:
                reader.read_bit()  # mvd_l1_zero_flag
            if pps.cabac_init_present:
                reader.read_bit()  # cabac_init_flag
            if temporal_mvp:
                collocated_from_l0 = True
                if slice_type == 0:
                    collocated_from_l0 = bool(reader.read_bit())
                collocated_count = num_l0 if collocated_from_l0 else num_l1
                if collocated_count > 1:
                    reader.read_ue()
            if (pps.weighted_pred and slice_type == 1) or (
                pps.weighted_bipred and slice_type == 0
            ):
                _skip_pred_weight_table(reader, sps, slice_type, num_l0, num_l1)
            reader.read_ue()  # five_minus_max_num_merge_cand
            if sps.motion_vector_resolution_control_idc == 2:
                reader.read_bit()

        reader.read_se()  # slice_qp_delta
        if pps.slice_chroma_qp_offsets_present:
            reader.read_se()  # slice_cb_qp_offset
            reader.read_se()  # slice_cr_qp_offset
        if pps.pps_slice_act_qp_offsets_present:
            # SCC syntax: slice_act_y/cb/cr_qp_offset.
            reader.read_se()
            reader.read_se()
            reader.read_se()
        if pps.chroma_qp_offset_list_enabled:
            reader.read_bit()  # cu_chroma_qp_offset_enabled_flag
        deblock_disabled = pps.deblocking_filter_disabled
        if pps.deblocking_filter_control_present:
            if pps.deblocking_filter_override_enabled:
                override = bool(reader.read_bit())
            else:
                override = False
            if override:
                deblock_disabled = bool(reader.read_bit())
                if not deblock_disabled:
                    reader.read_se()
                    reader.read_se()
        if pps.loop_filter_across_slices_enabled and (
            sao_luma or sao_chroma or not deblock_disabled
        ):
            reader.read_bit()

    entry_offsets: list[int] = []
    entry_start_bit = reader.bitpos
    if pps.tiles_enabled or pps.entropy_coding_sync_enabled:
        num_entry_points = reader.read_ue()
        if num_entry_points > reader.bits_left:
            raise ValueError("num_entry_point_offsets exceeds the remaining header")
        if num_entry_points:
            offset_len = reader.read_ue() + 1
            if offset_len > 32:
                raise ValueError("entry point offset length is too large")
            entry_offsets = [
                reader.read_bits(offset_len) + 1 for _ in range(num_entry_points)
            ]
    if pps.slice_header_extension_present:
        extension_bytes = reader.read_ue()
        reader.skip(extension_bytes * 8)
    entry_end_bit = reader.bitpos

    syntax_end_bit = reader.bitpos
    reader.read_byte_alignment()
    header_bits = reader.bitpos
    header_bytes = (header_bits + 7) // 8
    if header_bytes > len(rbsp):
        raise ValueError("slice header extends past the NAL RBSP")
    header_rbsp = rbsp[:header_bytes]
    return ParsedSliceHeader(
        pps_id=pps_id,
        first_in_pic=first_in_pic,
        dependent=dependent,
        address=address,
        slice_type=slice_type,
        poc_lsb=poc_lsb,
        entry_offsets=entry_offsets,
        header_bits=syntax_end_bit,
        header_rbsp=header_rbsp,
        slice_data_offset=header_bytes,
        prefix_end_bit=prefix_end_bit,
        body_start_bit=body_start_bit,
        entry_start_bit=entry_start_bit,
        entry_end_bit=entry_end_bit,
        has_address=has_address,
    )


def _skip_long_term_ref_pics(reader: BitReader, sps: Sps) -> int:
    if not sps.long_term_ref_pics_present:
        return 0
    num_sps = reader.read_ue()
    num_pics = reader.read_ue()
    if num_sps > sps.num_long_term_ref_pics_sps:
        raise ValueError("num_long_term_sps exceeds the SPS long-term table")
    used = sum(sps.long_term_used_by_curr_sps[:num_sps])
    for index in range(num_sps + num_pics):
        if index < num_sps:
            if num_sps > 1:
                reader.skip(_ceil_log2(num_sps))
        else:
            reader.skip(sps.log2_max_poc_lsb)
            used += reader.read_bit()
        if reader.read_bit():  # delta_poc_msb_present_flag
            reader.read_ue()
    return used
