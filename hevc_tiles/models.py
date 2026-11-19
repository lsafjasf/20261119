"""Data model shared by the parser, analyzer, and report renderer."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


VCL_TYPES = frozenset(range(0, 10)) | frozenset(range(16, 22))
IRAP_TYPES = frozenset({16, 17, 18, 19, 20, 21})
IDR_TYPES = frozenset({19, 20})
PARAMETER_SET_TYPES = frozenset({32, 33, 34})


@dataclass(slots=True)
class NalUnit:
    index: int
    file_offset: int
    start_code_length: int
    raw: bytes
    nal_type: int
    temporal_id: int
    payload: bytes
    rbsp: bytes

    @property
    def file_payload_offset(self) -> int:
        return self.file_offset + self.start_code_length + 2


@dataclass(slots=True)
class ShortTermRps:
    num_negative: int
    num_positive: int
    delta_pocs: list[int] = field(default_factory=list)
    used_by_curr: list[bool] = field(default_factory=list)


@dataclass(slots=True)
class Sps:
    sps_id: int
    profile_idc: int = 0
    max_sub_layers_minus1: int = 0
    chroma_format_idc: int = 1
    separate_colour_plane_flag: int = 0
    width: int = 0
    height: int = 0
    bit_depth_luma: int = 8
    bit_depth_chroma: int = 8
    log2_max_poc_lsb: int = 4
    log2_min_cb_size: int = 3
    log2_diff_max_min_cb_size: int = 0
    ctb_size: int = 64
    sao_enabled: bool = False
    long_term_ref_pics_present: bool = False
    num_long_term_ref_pics_sps: int = 0
    long_term_used_by_curr_sps: list[bool] = field(default_factory=list)
    temporal_mvp_enabled: bool = False
    short_term_rps: list[ShortTermRps] = field(default_factory=list)
    motion_vector_resolution_control_idc: int = 0
    sps_curr_pic_ref_enabled: bool = False

    @property
    def ctb_width(self) -> int:
        return (self.width + self.ctb_size - 1) // self.ctb_size

    @property
    def ctb_height(self) -> int:
        return (self.height + self.ctb_size - 1) // self.ctb_size


@dataclass(slots=True)
class Pps:
    pps_id: int
    sps_id: int
    dependent_slice_segments_enabled: bool = False
    output_flag_present: bool = False
    num_extra_slice_header_bits: int = 0
    cabac_init_present: bool = False
    num_ref_idx_l0_default: int = 1
    num_ref_idx_l1_default: int = 1
    init_qp_minus26: int = 0
    weighted_pred: bool = False
    weighted_bipred: bool = False
    slice_chroma_qp_offsets_present: bool = False
    tiles_enabled: bool = False
    entropy_coding_sync_enabled: bool = False
    num_tile_columns: int = 1
    num_tile_rows: int = 1
    uniform_spacing: bool = True
    column_width: list[int] = field(default_factory=lambda: [1])
    row_height: list[int] = field(default_factory=lambda: [1])
    loop_filter_across_tiles_enabled: bool = True
    loop_filter_across_tiles_flag_bit: int | None = None
    loop_filter_across_slices_enabled: bool = False
    deblocking_filter_control_present: bool = False
    deblocking_filter_override_enabled: bool = False
    deblocking_filter_disabled: bool = False
    beta_offset_div2: int = 0
    tc_offset_div2: int = 0
    lists_modification_present: bool = False
    slice_header_extension_present: bool = False
    chroma_qp_offset_list_enabled: bool = False
    pps_slice_act_qp_offsets_present: bool = False
    pps_curr_pic_ref_enabled: bool = False


@dataclass(slots=True)
class Tile:
    id: int
    column: int
    row: int
    x: int
    y: int
    width: int
    height: int
    ctb_x: int
    ctb_y: int
    ctb_width: int
    ctb_height: int
    ts_start: int
    ts_end: int


@dataclass(slots=True)
class TileLayout:
    ctb_size: int
    pic_width_in_ctbs: int
    pic_height_in_ctbs: int
    column_widths_ctbs: list[int]
    row_heights_ctbs: list[int]
    column_starts_ctbs: list[int]
    row_starts_ctbs: list[int]
    tiles: list[Tile]
    rs_to_ts: list[int]
    ts_to_rs: list[int]

    @property
    def num_columns(self) -> int:
        return len(self.column_widths_ctbs)

    @property
    def num_rows(self) -> int:
        return len(self.row_heights_ctbs)

    def tile_for_ts(self, address: int) -> Tile:
        if address < 0 or address >= len(self.ts_to_rs):
            raise ValueError(f"CTB tile-scan address {address} is out of range")
        for tile in self.tiles:
            if tile.ts_start <= address < tile.ts_end:
                return tile
        raise ValueError(f"no tile contains CTB tile-scan address {address}")

    def tile_for_rs(self, address: int) -> Tile:
        if address < 0 or address >= len(self.rs_to_ts):
            raise ValueError(f"CTB raster address {address} is out of range")
        return self.tile_for_ts(self.rs_to_ts[address])


@dataclass(slots=True)
class EntryPoint:
    index: int
    substream_bytes: int
    slice_data_offset: int
    file_offset: int | None


@dataclass(slots=True)
class ParsedSliceHeader:
    pps_id: int
    first_in_pic: bool
    dependent: bool
    address: int
    slice_type: int
    poc_lsb: int | None
    entry_offsets: list[int]
    header_bits: int
    header_rbsp: bytes
    slice_data_offset: int
    prefix_end_bit: int
    body_start_bit: int
    entry_start_bit: int
    entry_end_bit: int
    has_address: bool


@dataclass(slots=True)
class Slice:
    nal: NalUnit
    pps_id: int
    first_in_pic: bool
    dependent: bool
    address: int
    slice_type: int
    poc_lsb: int | None
    entry_points: list[EntryPoint]
    header_bits: int
    header_rbsp: bytes
    slice_data_offset: int
    tile_id: int
    prefix_end_bit: int = 0
    body_start_bit: int = 0
    entry_start_bit: int = 0
    entry_end_bit: int = 0
    has_address: bool = False
    au_index: int = 0

    @property
    def slice_type_name(self) -> str:
        return {0: "B", 1: "P", 2: "I"}.get(self.slice_type, f"?{self.slice_type}")


@dataclass(slots=True)
class TileMetric:
    tile_id: int
    entry_offsets: list[int] = field(default_factory=list)
    substream_bytes: list[int] = field(default_factory=list)
    absolute_offsets: list[int | None] = field(default_factory=list)
    slice_count: int = 0
    coded_bytes: int = 0
    samples: int = 0

    @property
    def average_bytes(self) -> float:
        return self.coded_bytes / self.samples if self.samples else 0.0

    @property
    def average_bits(self) -> float:
        return self.average_bytes * 8


@dataclass(slots=True)
class Dependency:
    source: int
    target: int
    kind: str
    detail: str


@dataclass(slots=True)
class StreamInfo:
    path: Path
    data: bytes
    nals: list[NalUnit]
    sps_by_id: dict[int, Sps]
    pps_by_id: dict[int, Pps]
    slices: list[Slice]
    layout: TileLayout | None
    sps: Sps | None
    pps: Pps | None
    warnings: list[str] = field(default_factory=list)

    def to_basic_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "bytes": len(self.data),
            "nal_units": len(self.nals),
            "sps_ids": sorted(self.sps_by_id),
            "pps_ids": sorted(self.pps_by_id),
            "slices": len(self.slices),
            "width": self.sps.width if self.sps else None,
            "height": self.sps.height if self.sps else None,
            "tile_columns": self.layout.num_columns if self.layout else 1,
            "tile_rows": self.layout.num_rows if self.layout else 1,
            "warnings": list(self.warnings),
        }
