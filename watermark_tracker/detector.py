"""Connected-component square marker detector with projective payload sampling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .codec import decode_code_bits
from .geometry import (
    Point,
    Quad,
    apply_homography,
    canonical_quad,
    convex_hull,
    homography_from_points,
    polygon_area,
    simplify_closed_polygon,
)
from .image import GrayImage, otsu_threshold
from .marker import DATA_SIZE, DATA_START, GRID_SIZE


@dataclass(frozen=True)
class DetectedMarker:
    marker_id: int
    corners: Quad
    confidence: float
    corrected_symbols: int
    orientation_quarter_turns: int


@dataclass
class _Component:
    points: list[tuple[int, int]]
    min_x: int
    min_y: int
    max_x: int
    max_y: int


def _connected_components(mask: bytearray, width: int, height: int) -> list[_Component]:
    visited = bytearray(len(mask))
    components: list[_Component] = []
    for start, enabled in enumerate(mask):
        if not enabled or visited[start]:
            continue
        stack = [start]
        visited[start] = 1
        points: list[tuple[int, int]] = []
        min_x = width
        min_y = height
        max_x = 0
        max_y = 0
        while stack:
            index = stack.pop()
            y, x = divmod(index, width)
            points.append((x, y))
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
            for dy in (-1, 0, 1):
                next_y = y + dy
                if next_y < 0 or next_y >= height:
                    continue
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    next_x = x + dx
                    if next_x < 0 or next_x >= width:
                        continue
                    neighbor = next_y * width + next_x
                    if mask[neighbor] and not visited[neighbor]:
                        visited[neighbor] = 1
                        stack.append(neighbor)
        components.append(
            _Component(points, min_x, min_y, max_x, max_y)
        )
    return components


def _perimeter(points: Sequence[Point]) -> float:
    return sum(
        current.distance_to(following)
        for current, following in zip(points, points[1:] + points[:1])
    )


def _fit_quad(points: Sequence[tuple[int, int]]) -> Quad | None:
    if len(points) < 16:
        return None
    hull = convex_hull(Point(x + 0.5, y + 0.5) for x, y in points)
    if len(hull) < 4:
        return None
    perimeter = _perimeter(hull)
    if perimeter < 4.0:
        return None
    candidates: list[list[Point]] = []
    for fraction in (0.012, 0.018, 0.025, 0.035, 0.05, 0.075, 0.10):
        simplified = simplify_closed_polygon(hull, perimeter * fraction)
        if len(simplified) == 4:
            candidates.append(simplified)
            break
        if len(simplified) > 4:
            candidates.append(simplified)
    if not candidates:
        return None
    selected = min(candidates, key=len)
    if len(selected) != 4:
        return None
    if polygon_area(selected) < 0:
        selected = list(reversed(selected))
    return Quad(*selected)


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _sample_marker_bits(
    image: GrayImage, quad: Quad
) -> tuple[tuple[int, ...], float, float, tuple[bool, ...]] | None:
    transform = homography_from_points(canonical_quad(GRID_SIZE), quad.corners)

    def sample(row: float, column: float, radius: float = 0.14) -> float:
        point = apply_homography(transform, Point(column, row))
        return image.sample_average(point.x, point.y, radius=radius, samples=3)

    border_values = []
    for column in range(2, GRID_SIZE - 2):
        border_values.append(sample(0.5, column + 0.5))
        border_values.append(sample(GRID_SIZE - 0.5, column + 0.5))
    for row in range(2, GRID_SIZE - 2):
        border_values.append(sample(row + 0.5, 0.5))
        border_values.append(sample(row + 0.5, GRID_SIZE - 0.5))
    margin_values = []
    for column in range(2, GRID_SIZE - 2):
        margin_values.append(sample(1.5, column + 0.5))
        margin_values.append(sample(GRID_SIZE - 1.5, column + 0.5))
    for row in range(2, GRID_SIZE - 2):
        margin_values.append(sample(row + 0.5, 1.5))
        margin_values.append(sample(row + 0.5, GRID_SIZE - 1.5))
    dark_level = _median(border_values)
    light_level = _median(margin_values)
    contrast = light_level - dark_level
    if contrast < 20.0:
        return None
    threshold = (dark_level + light_level) / 2.0
    anchor_positions = ((1.5, 1.5), (1.5, 10.5), (10.5, 1.5), (10.5, 10.5))
    anchors = tuple(
        sample(row, column, 0.16) < threshold
        for row, column in anchor_positions
    )
    bits: list[int] = []
    reliabilities: list[float] = []
    for row in range(DATA_SIZE):
        for column in range(DATA_SIZE):
            value = sample(row + DATA_START + 0.5, column + DATA_START + 0.5, 0.18)
            bits.append(1 if value < threshold else 0)
            reliabilities.append(
                min(1.0, abs(value - threshold) / max(1.0, contrast / 2.0))
            )
    return tuple(bits), sum(reliabilities) / len(reliabilities), contrast, anchors


def _mapped_orders(quad: Quad) -> list[tuple[int, tuple[Point, ...]]]:
    base = list(quad.corners)
    return [
        (shift, tuple(base[shift:] + base[:shift])) for shift in range(4)
    ]


def _decode_quad(
    image: GrayImage, quad: Quad
) -> DetectedMarker | None:
    best: DetectedMarker | None = None
    for shift, ordered in _mapped_orders(quad):
        sampled = _sample_marker_bits(image, Quad(*ordered))
        if sampled is None:
            continue
        bits, reliability, contrast, anchors = sampled
        if not anchors[0] or any(anchors[1:]):
            continue
        decoded = decode_code_bits(bits)
        if decoded is None:
            continue
        score = (
            0.58 * reliability
            + 0.30 * decoded.confidence
            + 0.12 * min(1.0, contrast / 120.0)
        )
        candidate = DetectedMarker(
            marker_id=decoded.payload.marker_id,
            corners=Quad(*ordered),
            confidence=max(0.0, min(1.0, score)),
            corrected_symbols=decoded.corrected_symbols,
            orientation_quarter_turns=shift,
        )
        if best is None or candidate.confidence > best.confidence:
            best = candidate
    return best


def detect_markers(
    image: GrayImage, threshold: int | None = None
) -> list[DetectedMarker]:
    """Detect decodable square markers in one grayscale frame."""
    if threshold is None:
        threshold = max(8, min(180, otsu_threshold(image)))
    mask = bytearray(1 if value <= threshold else 0 for value in image.data)
    detections: list[DetectedMarker] = []
    for component in _connected_components(mask, image.width, image.height):
        width = component.max_x - component.min_x + 1
        height = component.max_y - component.min_y + 1
        if len(component.points) < 80 or width < 18 or height < 18:
            continue
        quad = _fit_quad(component.points)
        if quad is None:
            continue
        area = quad.area()
        if area < 350.0:
            continue
        ratio = len(component.points) / area
        if ratio > 0.62 or ratio < 0.08:
            continue
        if quad.mean_side() < 20.0:
            continue
        detection = _decode_quad(image, quad)
        if detection is not None:
            detections.append(detection)
    detections.sort(key=lambda item: item.confidence, reverse=True)
    return detections
