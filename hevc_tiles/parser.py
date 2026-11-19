"""Annex B HEVC stream parser focused on tile and slice-segment metadata."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .bitstream import BitstreamError, ebsp_to_rbsp, rbsp_offset_to_ebsp
from .layout import build_tile_layout, slice_tile_ranges
from .models import (
    VCL_TYPES,
    EntryPoint,
    NalUnit,
    Slice,
    StreamInfo,
    TileMetric,
)
from .syntax import parse_pps, parse_slice_header, parse_sps


def split_annex_b(data: bytes) -> list[NalUnit]:
    starts: list[tuple[int, int]] = []
    index = 0
    size = len(data)
    while index + 3 <= size:
        if data[index : index + 3] == b"\x00\x00\x01":
            starts.append((index, 3))
            index += 3
        elif index + 4 <= size and data[index : index + 4] == b"\x00\x00\x00\x01":
            starts.append((index, 4))
            index += 4
        else:
            index += 1
    if not starts:
        raise ValueError("input is not an Annex B byte stream (no start code found)")

    nals: list[NalUnit] = []
    for nal_index, (start, sc_len) in enumerate(starts):
        end = starts[nal_index + 1][0] if nal_index + 1 < len(starts) else size
        raw = data[start:end]
        payload_start = sc_len
        if len(raw) < payload_start + 2:
            continue
        header = raw[payload_start : payload_start + 2]
        nal_type = (header[0] >> 1) & 0x3F
        temporal_id = header[1] & 0x07
        payload = raw[payload_start + 2 :]
        try:
            rbsp = ebsp_to_rbsp(payload)
        except BitstreamError:
            rbsp = payload
        nals.append(
            NalUnit(
                index=len(nals),
                file_offset=start,
                start_code_length=sc_len,
                raw=raw,
                nal_type=nal_type,
                temporal_id=temporal_id,
                payload=payload,
                rbsp=rbsp,
            )
        )
    return nals


def parse_stream(path: str | Path) -> StreamInfo:
    path = Path(path)
    data = path.read_bytes()
    nals = split_annex_b(data)
    sps_by_id = {}
    pps_by_id = {}
    parsed_slices: list[Slice] = []
    warnings: list[str] = []
    au_index = -1
    current_sps_id = None
    current_pps_id = None
    last_layout = None

    for nal in nals:
        try:
            if nal.nal_type == 33:
                sps = parse_sps(nal.rbsp)
                sps_by_id[sps.sps_id] = sps
            elif nal.nal_type == 34:
                sps_id = _peek_pps_sps_id(nal.rbsp)
                if sps_id not in sps_by_id:
                    raise ValueError(f"PPS references unknown SPS {sps_id}")
                pps = parse_pps(nal.rbsp, sps_by_id[sps_id])
                pps_by_id[pps.pps_id] = pps
            elif nal.nal_type in VCL_TYPES:
                if not sps_by_id or not pps_by_id:
                    warnings.append(
                        f"NAL {nal.index}: slice precedes the required parameter sets"
                    )
                    continue
                # The PPS id is the first Exp-Golomb after the first-slice flag
                # (and IRAP no-output flag), so parsing consumes the header.
                if nal.nal_type in (16, 17, 18, 19, 20, 21):
                    prefix_bits = 2
                else:
                    prefix_bits = 1
                pps = _find_pps_for_slice(nal.rbsp, pps_by_id, prefix_bits)
                sps = sps_by_id[pps.sps_id]
                header = parse_slice_header(nal.rbsp, nal.nal_type, pps, sps)
                if header.pps_id != pps.pps_id:
                    raise ValueError("slice PPS id changed while parsing")
                layout = build_tile_layout(sps, pps)
                if header.address >= len(layout.ts_to_rs):
                    raise ValueError(
                        f"slice_segment_address {header.address} exceeds picture"
                    )
                tile = layout.tile_for_rs(header.address)
                if header.first_in_pic:
                    au_index += 1
                if au_index < 0:
                    au_index = 0
                current_sps_id = sps.sps_id
                current_pps_id = pps.pps_id
                last_layout = layout
                parsed_slices.append(
                    Slice(
                        nal=nal,
                        pps_id=pps.pps_id,
                        first_in_pic=header.first_in_pic,
                        dependent=header.dependent,
                        address=header.address,
                        slice_type=header.slice_type,
                        poc_lsb=header.poc_lsb,
                        entry_points=_make_entry_points(
                            nal, header.entry_offsets, header.slice_data_offset
                        ),
                        header_bits=header.header_bits,
                        header_rbsp=header.header_rbsp,
                        slice_data_offset=header.slice_data_offset,
                        tile_id=tile.id,
                        prefix_end_bit=header.prefix_end_bit,
                        body_start_bit=header.body_start_bit,
                        entry_start_bit=header.entry_start_bit,
                        entry_end_bit=header.entry_end_bit,
                        has_address=header.has_address,
                        au_index=max(au_index, 0),
                    )
                )
        except (BitstreamError, ValueError) as error:
            warnings.append(f"NAL {nal.index} (type {nal.nal_type}): {error}")

    if current_sps_id is None or current_pps_id is None:
        raise ValueError("no usable VCL slice or parameter-set combination found")
    sps = sps_by_id[current_sps_id]
    pps = pps_by_id[current_pps_id]
    layout = last_layout or build_tile_layout(sps, pps)
    return StreamInfo(
        path=path,
        data=data,
        nals=nals,
        sps_by_id=sps_by_id,
        pps_by_id=pps_by_id,
        slices=parsed_slices,
        layout=layout,
        sps=sps,
        pps=pps,
        warnings=warnings,
    )


def _peek_pps_sps_id(rbsp: bytes) -> int:
    from .bitstream import BitReader

    reader = BitReader(rbsp, name="PPS")
    reader.read_ue()
    return reader.read_ue()


def _find_pps_for_slice(rbsp: bytes, pps_by_id: dict, prefix_bits: int):
    from .bitstream import BitReader

    reader = BitReader(rbsp, name="slice header")
    reader.skip(prefix_bits)
    pps_id = reader.read_ue()
    if pps_id not in pps_by_id:
        raise ValueError(f"slice references unknown PPS {pps_id}")
    return pps_by_id[pps_id]


def _make_entry_points(
    nal: NalUnit, substream_sizes: list[int], slice_data_offset: int
) -> list[EntryPoint]:
    if not substream_sizes:
        return []
    points: list[EntryPoint] = []
    offset = 0
    data_start_ebsp = rbsp_offset_to_ebsp(nal.payload, slice_data_offset)
    available_bytes = len(nal.payload) - data_start_ebsp
    if sum(substream_sizes) > available_bytes:
        raise ValueError(
            "entry point sizes exceed slice_segment_data payload"
        )
    for index, size in enumerate(substream_sizes):
        file_offset = nal.file_payload_offset + data_start_ebsp + offset
        points.append(
            EntryPoint(
                index=index,
                substream_bytes=size,
                slice_data_offset=offset,
                file_offset=file_offset,
            )
        )
        offset += size
    return points


def build_tile_metrics(info: StreamInfo) -> list[TileMetric]:
    if info.layout is None:
        return []
    metrics = {tile.id: TileMetric(tile.id) for tile in info.layout.tiles}
    slices_by_au: dict[int, list[Slice]] = defaultdict(list)
    for slice_ in info.slices:
        slices_by_au[slice_.au_index].append(slice_)

    for slices in slices_by_au.values():
        counts = slice_tile_ranges(info.layout, slices)
        for tile_id, count in counts.items():
            metrics[tile_id].slice_count += count

        ordered_starts = sorted(
            {info.layout.rs_to_ts[slice_.address] for slice_ in slices} | {0}
        )
        ends = ordered_starts[1:] + [len(info.layout.ts_to_rs)]
        end_by_start = dict(zip(ordered_starts, ends))
        for slice_ in slices:
            entry_sizes = [point.substream_bytes for point in slice_.entry_points]
            data_start_ebsp = rbsp_offset_to_ebsp(
                slice_.nal.payload, slice_.slice_data_offset
            )
            data_bytes = max(0, len(slice_.nal.payload) - data_start_ebsp)
            if entry_sizes:
                substreams = entry_sizes + [max(0, data_bytes - sum(entry_sizes))]
            else:
                substreams = [data_bytes]
            start_ts = info.layout.rs_to_ts[slice_.address]
            tile_sequence = _tiles_for_slice(
                info, slice_, end_by_start.get(start_ts)
            )
            if not tile_sequence:
                tile_sequence = [slice_.tile_id]
            for index, size in enumerate(substreams):
                tile_id = tile_sequence[min(index, len(tile_sequence) - 1)]
                metric = metrics[tile_id]
                cumulative = sum(substreams[:index])
                metric.entry_offsets.append(cumulative)
                metric.substream_bytes.append(size)
                point = slice_.entry_points[index] if index < len(slice_.entry_points) else None
                if point is not None and point.file_offset is not None:
                    absolute = point.file_offset
                else:
                    absolute = slice_.nal.file_payload_offset + data_start_ebsp + cumulative
                metric.absolute_offsets.append(absolute)
                metric.coded_bytes += size
                metric.samples += 1
    return [metrics[tile.id] for tile in info.layout.tiles]


def _tiles_for_slice(info: StreamInfo, slice_: Slice, end_ts: int | None) -> list[int]:
    if info.layout is None:
        return []
    assert info.layout is not None
    start_ts = info.layout.rs_to_ts[slice_.address]
    if end_ts is None or end_ts <= start_ts:
        return [slice_.tile_id]
    result = []
    for tile in info.layout.tiles:
        if max(start_ts, tile.ts_start) < min(end_ts, tile.ts_end):
            result.append(tile.id)
    return result
