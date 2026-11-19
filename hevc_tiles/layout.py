"""HEVC tile-grid construction and CTB scan-order mapping."""

from __future__ import annotations

from .models import Pps, Sps, Tile, TileLayout


def _uniform_sizes(total: int, count: int) -> list[int]:
    return [(i + 1) * total // count - i * total // count for i in range(count)]


def build_tile_layout(sps: Sps, pps: Pps) -> TileLayout:
    if not pps.tiles_enabled:
        column_widths = [sps.ctb_width]
        row_heights = [sps.ctb_height]
        num_columns = 1
        num_rows = 1
    else:
        num_columns = pps.num_tile_columns
        num_rows = pps.num_tile_rows
        column_widths = (
            _uniform_sizes(sps.ctb_width, num_columns)
            if pps.uniform_spacing
            else list(pps.column_width)
        )
        row_heights = (
            _uniform_sizes(sps.ctb_height, num_rows)
            if pps.uniform_spacing
            else list(pps.row_height)
        )

    if sum(column_widths) != sps.ctb_width:
        raise ValueError(
            f"tile columns cover {sum(column_widths)} CTBs, expected {sps.ctb_width}"
        )
    if sum(row_heights) != sps.ctb_height:
        raise ValueError(
            f"tile rows cover {sum(row_heights)} CTBs, expected {sps.ctb_height}"
        )
    if any(value <= 0 for value in column_widths + row_heights):
        raise ValueError("tile dimensions must be positive")

    column_starts = [sum(column_widths[:i]) for i in range(num_columns)]
    row_starts = [sum(row_heights[:i]) for i in range(num_rows)]

    # Build CtbAddrRsToTs first because the tile-scan numbering is defined by
    # visiting every CTB in each tile, tile row by tile row.
    rs_to_ts = [-1] * (sps.ctb_width * sps.ctb_height)
    ts_to_rs: list[int] = []
    tile_ranges: list[tuple[int, int]] = []
    for tile_row in range(num_rows):
        for tile_column in range(num_columns):
            start = len(ts_to_rs)
            for local_y in range(row_heights[tile_row]):
                rs_y = row_starts[tile_row] + local_y
                for local_x in range(column_widths[tile_column]):
                    rs_x = column_starts[tile_column] + local_x
                    rs = rs_y * sps.ctb_width + rs_x
                    rs_to_ts[rs] = len(ts_to_rs)
                    ts_to_rs.append(rs)
            tile_ranges.append((start, len(ts_to_rs)))

    tiles: list[Tile] = []
    for tile_row in range(num_rows):
        for tile_column in range(num_columns):
            tile_id = tile_row * num_columns + tile_column
            ctb_x = column_starts[tile_column]
            ctb_y = row_starts[tile_row]
            ctb_w = column_widths[tile_column]
            ctb_h = row_heights[tile_row]
            ts_start, ts_end = tile_ranges[tile_id]
            tiles.append(
                Tile(
                    id=tile_id,
                    column=tile_column,
                    row=tile_row,
                    x=ctb_x * sps.ctb_size,
                    y=ctb_y * sps.ctb_size,
                    width=min(ctb_w * sps.ctb_size, sps.width - ctb_x * sps.ctb_size),
                    height=min(
                        ctb_h * sps.ctb_size, sps.height - ctb_y * sps.ctb_size
                    ),
                    ctb_x=ctb_x,
                    ctb_y=ctb_y,
                    ctb_width=ctb_w,
                    ctb_height=ctb_h,
                    ts_start=ts_start,
                    ts_end=ts_end,
                )
            )

    return TileLayout(
        ctb_size=sps.ctb_size,
        pic_width_in_ctbs=sps.ctb_width,
        pic_height_in_ctbs=sps.ctb_height,
        column_widths_ctbs=column_widths,
        row_heights_ctbs=row_heights,
        column_starts_ctbs=column_starts,
        row_starts_ctbs=row_starts,
        tiles=tiles,
        rs_to_ts=rs_to_ts,
        ts_to_rs=ts_to_rs,
    )


def slice_tile_ranges(layout: TileLayout, slices: list) -> dict[int, int]:
    """Count slice intervals that intersect each tile.

    Slice segment addresses are in CTB tile-scan order. A slice extends to the
    next independent slice start, or to the end of the picture.
    """

    counts = {tile.id: 0 for tile in layout.tiles}
    if not slices:
        return counts
    starts = sorted({layout.rs_to_ts[slice_.address] for slice_ in slices})
    if 0 not in starts:
        starts.insert(0, 0)
    starts.append(len(layout.ts_to_rs))
    intervals = list(zip(starts, starts[1:]))
    for start, end in intervals:
        for tile in layout.tiles:
            if max(start, tile.ts_start) < min(end, tile.ts_end):
                counts[tile.id] += 1
    return counts
