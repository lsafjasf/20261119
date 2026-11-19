"""Partial HEVC access-unit construction and pixel verification."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .bitstream import ebsp_to_rbsp_index_map, rbsp_offset_to_ebsp, rbsp_to_ebsp
from .models import IDR_TYPES, Slice, StreamInfo, Tile


class DecodeError(RuntimeError):
    pass


@dataclass(slots=True)
class DecodedFrame:
    width: int
    height: int
    chroma_format: int
    bytes_per_sample: int
    data: bytes

    @property
    def luma_bytes(self) -> int:
        return self.width * self.height * self.bytes_per_sample

    @property
    def chroma_dimensions(self) -> tuple[int, int]:
        if self.chroma_format == 1:
            return (self.width + 1) // 2, (self.height + 1) // 2
        if self.chroma_format == 2:
            return (self.width + 1) // 2, self.height
        if self.chroma_format == 3:
            return self.width, self.height
        return (0, 0)

    @property
    def expected_bytes(self) -> int:
        chroma_w, chroma_h = self.chroma_dimensions
        multiplier = 2 if self.chroma_format else 0
        return (
            self.width * self.height
            + multiplier * chroma_w * chroma_h
        ) * self.bytes_per_sample


@dataclass(slots=True)
class TileComparison:
    tile_id: int
    samples: int
    mismatches: int
    max_error: int
    mean_error: float

    @property
    def exact(self) -> bool:
        return self.mismatches == 0


@dataclass(slots=True)
class Verification:
    full_frame: DecodedFrame
    partial_frame: DecodedFrame | None
    comparisons: dict[int, TileComparison] = field(default_factory=dict)
    partial_stream: bytes | None = None
    decoder: str | None = None
    error: str | None = None

    @property
    def exact(self) -> bool:
        return (
            self.error is None
            and self.partial_frame is not None
            and bool(self.comparisons)
            and all(item.exact for item in self.comparisons.values())
        )


def find_ffmpeg(explicit: str | None = None) -> str:
    candidates = [
        explicit,
        os.environ.get("HEVC_FFMPEG"),
        shutil.which("ffmpeg"),
        "/tmp/ffmpeg70",
        "/tmp/ffmpeg-7.0.2-amd64-static/ffmpeg",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise DecodeError(
        "找不到可执行的 ffmpeg；请用 --ffmpeg 指定，或设置 HEVC_FFMPEG"
    )


def decode_annex_b(
    ffmpeg: str,
    stream: bytes | Path,
    *,
    width: int,
    height: int,
    chroma_format: int,
    bit_depth: int,
    frame_index: int = 0,
) -> DecodedFrame:
    if chroma_format not in (0, 1, 2, 3):
        raise DecodeError(f"不支持的 chroma_format_idc={chroma_format}")
    bytes_per_sample = 1 if bit_depth <= 8 else 2
    pix_fmt = {
        (0, 1): "gray",
        (1, 1): "yuv420p",
        (2, 1): "yuv422p",
        (3, 1): "yuv444p",
        (0, 2): "gray16le",
        (1, 2): "yuv420p10le",
        (2, 2): "yuv422p10le",
        (3, 2): "yuv444p10le",
    }[(chroma_format, bytes_per_sample)]
    with tempfile.TemporaryDirectory(prefix="hevc-tile-decode-") as temp_dir:
        input_path = Path(temp_dir) / "input.hevc"
        if isinstance(stream, bytes):
            input_path.write_bytes(stream)
        else:
            input_path = Path(stream)
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-threads",
            "1",
            "-i",
            str(input_path),
        ]
        if frame_index:
            command.extend(
                ["-vf", f"select=eq(n\\,{frame_index})", "-fps_mode", "passthrough"]
            )
        command.extend(
            [
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                pix_fmt,
                "pipe:1",
            ]
        )
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
    if result.returncode != 0 and not result.stdout:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise DecodeError(f"ffmpeg 解码失败: {detail or '无输出'}")
    chroma_w, chroma_h = (
        ((width + 1) // 2, (height + 1) // 2)
        if chroma_format == 1
        else ((width + 1) // 2, height)
        if chroma_format == 2
        else (width, height)
        if chroma_format == 3
        else (0, 0)
    )
    expected = (
        width * height + (2 * chroma_w * chroma_h if chroma_format else 0)
    ) * bytes_per_sample
    if len(result.stdout) < expected:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise DecodeError(
            f"解码输出 {len(result.stdout)} 字节，期望 {expected} 字节"
            + (f": {detail}" if detail else "")
        )
    return DecodedFrame(
        width=width,
        height=height,
        chroma_format=chroma_format,
        bytes_per_sample=bytes_per_sample,
        data=result.stdout[:expected],
    )


def build_partial_access_unit(
    info: StreamInfo, frame_slices: list[Slice], available: set[int]
) -> bytes:
    if not available:
        raise DecodeError("至少需要一个可用瓦片")
    if info.layout is None:
        raise DecodeError("流中没有瓦片布局")
    by_address = sorted(
        frame_slices,
        key=lambda item: info.layout.rs_to_ts[item.address] if info.layout else 0,
    )
    if not by_address or not by_address[0].first_in_pic:
        raise DecodeError("当前帧没有首切片")

    coverage = _slice_coverage(info, frame_slices)
    mapped_tiles = {
        tile_id for tile_ids in coverage.values() for tile_id in tile_ids
    }
    missing = sorted(available - mapped_tiles)
    if missing:
        raise DecodeError(f"所选瓦片 {missing} 没有对应切片或子流")

    prefix = [
        nal.raw
        for nal in info.nals
        if nal.nal_type in (32, 33, 34)
    ]
    body: list[bytes] = []
    if by_address[0].tile_id not in available:
        source = next(
            (item for item in by_address if 0 in coverage[id(item)]),
            by_address[0],
        )
        body.append(_synthesize_slice(info, source, None, b"", first=True))

    for slice_ in by_address:
        covered = coverage[id(slice_)]
        if not covered:
            continue
        sizes = _all_substream_sizes(slice_)
        starts = [0]
        for size in sizes[:-1]:
            starts.append(starts[-1] + size)
        for position, tile_id in enumerate(covered):
            if tile_id not in available:
                continue
            tile = info.layout.tiles[tile_id]
            if len(covered) > 1:
                address = info.layout.ts_to_rs[tile.ts_start]
            else:
                address = slice_.address
            data = _substream_data(
                slice_, starts[position], starts[position] + sizes[position]
            )
            body.append(
                _synthesize_slice(
                    info,
                    slice_,
                    address,
                    data,
                    first=tile_id == 0 and slice_ is by_address[0],
                )
            )
    return b"".join(prefix + body)


def _slice_coverage(info: StreamInfo, slices: list[Slice]) -> dict[int, list[int]]:
    assert info.layout is not None
    ordered = sorted(slices, key=lambda item: info.layout.rs_to_ts[item.address])
    coverage: dict[int, list[int]] = {}
    for index, slice_ in enumerate(ordered):
        start_ts = info.layout.rs_to_ts[slice_.address]
        end_ts = (
            info.layout.rs_to_ts[ordered[index + 1].address]
            if index + 1 < len(ordered)
            else len(info.layout.ts_to_rs)
        )
        covered = [
            tile.id
            for tile in info.layout.tiles
            if max(start_ts, tile.ts_start) < min(end_ts, tile.ts_end)
        ]
        if not covered:
            continue
        substreams = _all_substream_sizes(slice_)
        if len(covered) > 1:
            first_tile = info.layout.tiles[covered[0]]
            if start_ts != first_tile.ts_start:
                raise DecodeError(
                    f"切片 @TS{start_ts} 从瓦片中间开始，无法安全改写为瓦片局部切片"
                )
            if len(substreams) != len(covered):
                raise DecodeError(
                    f"切片 @TS{start_ts} 覆盖 {len(covered)} 个瓦片，"
                    f"但有 {len(substreams)} 个子流；无法确定 entry point 映射"
                )
        elif len(substreams) != 1:
            raise DecodeError(
                f"切片 @TS{start_ts} 位于单个瓦片内，却有 {len(substreams)} 个子流"
            )
        coverage[id(slice_)] = covered
    return coverage


def _all_substream_sizes(slice_: Slice) -> list[int]:
    sizes = [point.substream_bytes for point in slice_.entry_points]
    data_bytes = _slice_data_length(slice_)
    if sizes:
        return sizes + [max(0, data_bytes - sum(sizes))]
    return [data_bytes]


def _slice_data_length(slice_: Slice) -> int:
    data_start = rbsp_offset_to_ebsp(slice_.nal.payload, slice_.slice_data_offset)
    return max(0, len(slice_.nal.payload) - data_start)


def _substream_data(slice_: Slice, start: int, end: int) -> bytes:
    data_start = rbsp_offset_to_ebsp(slice_.nal.payload, slice_.slice_data_offset)
    if start < 0 or end < start or data_start + end > len(slice_.nal.payload):
        raise DecodeError("entry point 子流超出 slice_segment_data 范围")
    mapping = ebsp_to_rbsp_index_map(slice_.nal.payload)
    begin = mapping[data_start + start]
    finish = mapping[data_start + end]
    return slice_.nal.rbsp[begin:finish]


def _synthesize_slice(
    info: StreamInfo,
    source: Slice,
    address: int | None,
    data: bytes,
    *,
    first: bool,
) -> bytes:
    bits = _bytes_to_bits(source.header_rbsp)
    if source.prefix_end_bit <= 0 or source.body_start_bit > source.entry_start_bit:
        raise DecodeError("切片头位置信息不足，无法重写")
    prefix = bits[1 : source.prefix_end_bit]
    output = [1 if first else 0] + prefix
    if not first:
        if address is None:
            raise DecodeError("非首切片缺少 slice_segment_address")
        assert info.sps is not None
        width = max(0, (info.sps.ctb_width * info.sps.ctb_height - 1).bit_length())
        output.extend(_int_to_bits(address, width))
    output.extend(bits[source.body_start_bit : source.entry_start_bit])
    # The rewritten slice has exactly one substream. With tiles enabled the
    # syntax still requires num_entry_point_offsets, encoded here as ue(0).
    output.append(1)
    output.append(1)
    while len(output) % 8:
        output.append(0)
    header = _bits_to_bytes(output)
    nal = source.nal
    nal_header = nal.raw[nal.start_code_length : nal.start_code_length + 2]
    return b"\x00\x00\x00\x01" + nal_header + rbsp_to_ebsp(header + data)


def _bytes_to_bits(data: bytes) -> list[int]:
    return [(byte >> (7 - index)) & 1 for byte in data for index in range(8)]


def _int_to_bits(value: int, width: int) -> list[int]:
    if width < 0 or value < 0 or value >= (1 << width):
        raise DecodeError(f"值 {value} 无法放入 {width} 位地址")
    return [(value >> (width - 1 - index)) & 1 for index in range(width)]


def _bits_to_bytes(bits: list[int]) -> bytes:
    if len(bits) % 8:
        raise DecodeError("内部错误：切片头未按字节对齐")
    result = bytearray()
    for index in range(0, len(bits), 8):
        value = 0
        for bit in bits[index : index + 8]:
            value = (value << 1) | bit
        result.append(value)
    return bytes(result)


def verify_tiles(
    info: StreamInfo,
    frame_slices: list[Slice],
    available: set[int],
    *,
    frame_index: int = 0,
    ffmpeg: str | None = None,
) -> Verification:
    if info.sps is None or info.layout is None:
        raise DecodeError("流中没有可用的 SPS/瓦片布局")
    executable = find_ffmpeg(ffmpeg)
    full = decode_annex_b(
        executable,
        info.path,
        width=info.sps.width,
        height=info.sps.height,
        chroma_format=info.sps.chroma_format_idc,
        bit_depth=info.sps.bit_depth_luma,
        frame_index=frame_index,
    )
    if info.sps.chroma_format_idc != 1 or info.sps.bit_depth_luma != 8:
        return Verification(
            full_frame=full,
            decoder=executable,
            error="像素核验当前仅支持 8-bit 4:2:0 测试流",
        )
    try:
        partial_stream = build_partial_access_unit(info, frame_slices, available)
        partial = decode_annex_b(
            executable,
            partial_stream,
            width=info.sps.width,
            height=info.sps.height,
            chroma_format=info.sps.chroma_format_idc,
            bit_depth=info.sps.bit_depth_luma,
            frame_index=0,
        )
    except (DecodeError, ValueError) as error:
        return Verification(full_frame=full, decoder=executable, error=str(error))

    tiles_by_id = {tile.id: tile for tile in info.layout.tiles}
    comparisons = {
        tile_id: compare_tile(full, partial, tiles_by_id[tile_id])
        for tile_id in sorted(available)
    }
    return Verification(
        full_frame=full,
        partial_frame=partial,
        comparisons=comparisons,
        partial_stream=partial_stream,
        decoder=executable,
    )


def compare_tile(
    full: DecodedFrame, partial: DecodedFrame, tile: Tile
) -> TileComparison:
    if (
        full.width != partial.width
        or full.height != partial.height
        or full.bytes_per_sample != partial.bytes_per_sample
    ):
        raise DecodeError("完整帧与局部帧的尺寸/采样格式不一致")
    x0, x1 = tile.x, tile.x + tile.width
    y0, y1 = tile.y, tile.y + tile.height
    chroma_w, _ = full.chroma_dimensions
    luma_size = full.width * full.height
    mismatches = 0
    samples = 0
    error_sum = 0
    max_error = 0

    # Luma is compared with direct strides.
    for y in range(y0, y1):
        start = y * full.width + x0
        end = y * full.width + x1
        for left, right in zip(full.data[start:end], partial.data[start:end]):
            delta = abs(left - right)
            samples += 1
            error_sum += delta
            max_error = max(max_error, delta)
            mismatches += delta != 0

    if full.chroma_format == 1:
        cx0, cx1 = x0 // 2, (x1 + 1) // 2
        cy0, cy1 = y0 // 2, (y1 + 1) // 2
        for plane in (0, 1):
            base = luma_size + plane * chroma_w * ((full.height + 1) // 2)
            for y in range(cy0, cy1):
                start = base + y * chroma_w + cx0
                end = base + y * chroma_w + cx1
                for left, right in zip(full.data[start:end], partial.data[start:end]):
                    delta = abs(left - right)
                    samples += 1
                    error_sum += delta
                    max_error = max(max_error, delta)
                    mismatches += delta != 0
    return TileComparison(
        tile_id=tile.id,
        samples=samples,
        mismatches=mismatches,
        max_error=max_error,
        mean_error=error_sum / samples if samples else 0.0,
    )
