"""Square marker geometry and rendering."""

from __future__ import annotations

from .codec import bits_to_matrix, encode_payload
from .geometry import Point, Quad, apply_homography, canonical_quad, homography_from_points
from .image import GrayImage

GRID_SIZE = 12
DATA_START = 2
DATA_SIZE = 8
ORIENTATION_ANCHOR = (1, 1)
BLACK_LEVEL = 20
WHITE_LEVEL = 245


def marker_bits(marker_id: int) -> tuple[int, ...]:
    return encode_payload(marker_id)


def marker_data_matrix(marker_id: int) -> tuple[tuple[int, ...], ...]:
    return bits_to_matrix(marker_bits(marker_id), DATA_SIZE)


def marker_cell_polygon(row: int, column: int) -> tuple[Point, Point, Point, Point]:
    return (
        Point(column, row),
        Point(column + 1, row),
        Point(column + 1, row + 1),
        Point(column, row + 1),
    )


def _cell_is_black(row: int, column: int, data: tuple[tuple[int, ...], ...]) -> bool:
    if row < DATA_START or column < DATA_START:
        return False
    if row >= DATA_START + DATA_SIZE or column >= DATA_START + DATA_SIZE:
        return False
    return data[row - DATA_START][column - DATA_START] == 1


def render_marker(
    image: GrayImage,
    corners: Quad | tuple[Point, Point, Point, Point],
    marker_id: int,
    dark: int = BLACK_LEVEL,
    light: int = WHITE_LEVEL,
    supersample: int = 2,
) -> None:
    """Render the marker using a projective map from the 12x12 module grid."""
    quad = corners if isinstance(corners, Quad) else Quad(*corners)
    transform = homography_from_points(canonical_quad(GRID_SIZE), quad.corners)

    def mapped(points: tuple[Point, ...]) -> list[Point]:
        return [apply_homography(transform, point) for point in points]

    image.fill_polygon(mapped(canonical_quad(GRID_SIZE)), light, supersample)
    border_strips = [
        (
            Point(0, 0),
            Point(GRID_SIZE, 0),
            Point(GRID_SIZE, 1),
            Point(0, 1),
        ),
        (
            Point(0, GRID_SIZE - 1),
            Point(GRID_SIZE, GRID_SIZE - 1),
            Point(GRID_SIZE, GRID_SIZE),
            Point(0, GRID_SIZE),
        ),
        (
            Point(0, 0),
            Point(1, 0),
            Point(1, GRID_SIZE),
            Point(0, GRID_SIZE),
        ),
        (
            Point(GRID_SIZE - 1, 0),
            Point(GRID_SIZE, 0),
            Point(GRID_SIZE, GRID_SIZE),
            Point(GRID_SIZE - 1, GRID_SIZE),
        ),
    ]
    for strip in border_strips:
        image.fill_polygon(mapped(strip), dark, supersample)

    anchor_row, anchor_column = ORIENTATION_ANCHOR
    image.fill_polygon(
        mapped(marker_cell_polygon(anchor_row, anchor_column)), dark, supersample
    )
    data = marker_data_matrix(marker_id)
    for row in range(DATA_SIZE):
        for column in range(DATA_SIZE):
            if not data[row][column]:
                continue
            polygon = marker_cell_polygon(row + DATA_START, column + DATA_START)
            image.fill_polygon(mapped(polygon), dark, supersample)
