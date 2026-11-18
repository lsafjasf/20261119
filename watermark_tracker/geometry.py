"""Small dependency-free projective geometry helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def distance_to(self, other: "Point") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)


@dataclass(frozen=True)
class Quad:
    """Quadrilateral ordered top-left, top-right, bottom-right, bottom-left."""

    tl: Point
    tr: Point
    br: Point
    bl: Point

    @property
    def corners(self) -> tuple[Point, Point, Point, Point]:
        return (self.tl, self.tr, self.br, self.bl)

    def center(self) -> Point:
        return Point(
            sum(p.x for p in self.corners) / 4.0,
            sum(p.y for p in self.corners) / 4.0,
        )

    def area(self) -> float:
        return abs(polygon_area(self.corners))

    def mean_side(self) -> float:
        sides = [
            self.tl.distance_to(self.tr),
            self.tr.distance_to(self.br),
            self.br.distance_to(self.bl),
            self.bl.distance_to(self.tl),
        ]
        return sum(sides) / 4.0

    def as_tuple(self) -> tuple[tuple[float, float], ...]:
        return tuple((p.x, p.y) for p in self.corners)

    @classmethod
    def from_points(cls, points: Sequence[Sequence[float] | Point]) -> "Quad":
        if len(points) != 4:
            raise ValueError("a quad needs exactly four points")
        parsed = [
            p if isinstance(p, Point) else Point(float(p[0]), float(p[1]))
            for p in points
        ]
        return cls(parsed[0], parsed[1], parsed[2], parsed[3])


def polygon_area(points: Sequence[Point]) -> float:
    area = 0.0
    for current, following in zip(points, points[1:] + points[:1]):
        area += current.x * following.y - following.x * current.y
    return 0.5 * area


def polygon_centroid(points: Sequence[Point]) -> Point:
    cross_sum = 0.0
    x_sum = 0.0
    y_sum = 0.0
    for current, following in zip(points, points[1:] + points[:1]):
        cross = current.x * following.y - following.x * current.y
        cross_sum += cross
        x_sum += (current.x + following.x) * cross
        y_sum += (current.y + following.y) * cross
    if abs(cross_sum) < 1e-9:
        return Point(
            sum(p.x for p in points) / len(points),
            sum(p.y for p in points) / len(points),
        )
    return Point(x_sum / (3.0 * cross_sum), y_sum / (3.0 * cross_sum))


def solve_linear(matrix: list[list[float]], values: list[float]) -> list[float]:
    """Solve a small dense linear system with partial pivoting."""
    size = len(values)
    augmented = [row[:] + [value] for row, value in zip(matrix, values)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(augmented[r][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("singular linear system")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        for index in range(column, size + 1):
            augmented[column][index] /= divisor
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor == 0.0:
                continue
            for index in range(column, size + 1):
                augmented[row][index] -= factor * augmented[column][index]
    return [augmented[index][size] for index in range(size)]


def homography_from_points(
    source: Sequence[Point], destination: Sequence[Point]
) -> tuple[float, ...]:
    """Return an 8-element homography (row major, h33 fixed to one)."""
    if len(source) != 4 or len(destination) != 4:
        raise ValueError("homography needs four point pairs")
    matrix: list[list[float]] = []
    values: list[float] = []
    for src, dst in zip(source, destination):
        matrix.append(
            [src.x, src.y, 1.0, 0.0, 0.0, 0.0, -dst.x * src.x, -dst.x * src.y]
        )
        values.append(dst.x)
        matrix.append(
            [0.0, 0.0, 0.0, src.x, src.y, 1.0, -dst.y * src.x, -dst.y * src.y]
        )
        values.append(dst.y)
    return tuple(solve_linear(matrix, values))


def apply_homography(h: Sequence[float], point: Point) -> Point:
    denominator = h[6] * point.x + h[7] * point.y + 1.0
    if abs(denominator) < 1e-12:
        raise ValueError("point mapped to infinity")
    return Point(
        (h[0] * point.x + h[1] * point.y + h[2]) / denominator,
        (h[3] * point.x + h[4] * point.y + h[5]) / denominator,
    )


def canonical_quad(size: float) -> tuple[Point, Point, Point, Point]:
    return (
        Point(0.0, 0.0),
        Point(size, 0.0),
        Point(size, size),
        Point(0.0, size),
    )


def homography_for_quad(quad: Quad, size: float) -> tuple[float, ...]:
    return homography_from_points(canonical_quad(size), quad.corners)


def quad_from_center(
    center: Point,
    size: float,
    angle_radians: float = 0.0,
    perspective: float = 0.0,
) -> Quad:
    """Build a convex projective-looking quad around a center point."""
    half = size / 2.0
    local = [
        (-half * (1.0 + perspective), -half),
        (half * (1.0 - perspective), -half),
        (half * (1.0 + perspective), half),
        (-half * (1.0 - perspective), half),
    ]
    cosine = math.cos(angle_radians)
    sine = math.sin(angle_radians)
    points = [
        Point(center.x + x * cosine - y * sine, center.y + x * sine + y * cosine)
        for x, y in local
    ]
    return Quad(*points)


def convex_hull(points: Iterable[Point]) -> list[Point]:
    unique = sorted({(round(p.x, 6), round(p.y, 6)) for p in points})
    if len(unique) <= 1:
        return [Point(x, y) for x, y in unique]

    def cross(origin: tuple[float, float], a: tuple[float, float], b: tuple[float, float]):
        return (a[0] - origin[0]) * (b[1] - origin[1]) - (
            a[1] - origin[1]
        ) * (b[0] - origin[0])

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return [Point(x, y) for x, y in lower[:-1] + upper[:-1]]


def _point_line_distance(point: Point, start: Point, end: Point) -> float:
    length = start.distance_to(end)
    if length < 1e-9:
        return point.distance_to(start)
    return abs(
        (end.x - start.x) * (start.y - point.y)
        - (start.x - point.x) * (end.y - start.y)
    ) / length


def rdp(points: Sequence[Point], epsilon: float) -> list[Point]:
    if len(points) <= 2:
        return list(points)
    start, end = points[0], points[-1]
    index, distance = max(
        ((i, _point_line_distance(p, start, end)) for i, p in enumerate(points[1:-1], 1)),
        default=(0, 0.0),
        key=lambda item: item[1],
    )
    if distance <= epsilon:
        return [start, end]
    left = rdp(points[: index + 1], epsilon)
    right = rdp(points[index:], epsilon)
    return left[:-1] + right


def simplify_closed_polygon(points: Sequence[Point], epsilon: float) -> list[Point]:
    if len(points) < 4:
        return list(points)
    farthest_a = 0
    farthest_b = 0
    farthest_distance = -1.0
    for a in range(len(points)):
        for b in range(a + 1, len(points)):
            distance = points[a].distance_to(points[b])
            if distance > farthest_distance:
                farthest_a, farthest_b = a, b
                farthest_distance = distance
    first = list(points[farthest_a : farthest_b + 1])
    second = list(points[farthest_b:] + points[: farthest_a + 1])
    simplified_a = rdp(first, epsilon)
    simplified_b = rdp(second, epsilon)
    combined = simplified_a[:-1] + simplified_b[:-1]
    return combined


def polygon_inside_clip(
    subject: Sequence[Point], clip: Sequence[Point]
) -> list[Point]:
    """Sutherland-Hodgman clip for convex polygons."""
    output = list(subject)
    orientation = 1.0 if polygon_area(clip) > 0 else -1.0
    for index, clip_start in enumerate(clip):
        clip_end = clip[(index + 1) % len(clip)]
        input_points = output
        output = []
        if not input_points:
            break

        def inside(point: Point) -> bool:
            cross = (
                (clip_end.x - clip_start.x) * (point.y - clip_start.y)
                - (clip_end.y - clip_start.y) * (point.x - clip_start.x)
            )
            return cross * orientation >= -1e-9

        def intersection(a: Point, b: Point) -> Point:
            x1, y1, x2, y2 = a.x, a.y, b.x, b.y
            x3, y3, x4, y4 = clip_start.x, clip_start.y, clip_end.x, clip_end.y
            denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
            if abs(denominator) < 1e-12:
                return b
            t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denominator
            return Point(x1 + t * (x2 - x1), y1 + t * (y2 - y1))

        previous = input_points[-1]
        for current in input_points:
            previous_inside = inside(previous)
            current_inside = inside(current)
            if current_inside:
                if not previous_inside:
                    output.append(intersection(previous, current))
                output.append(current)
            elif previous_inside:
                output.append(intersection(previous, current))
            previous = current
    return output


def overlap_fraction(subject: Sequence[Point], other: Sequence[Point]) -> float:
    subject_area = abs(polygon_area(subject))
    if subject_area < 1e-9:
        return 0.0
    overlap = polygon_inside_clip(subject, other)
    return min(1.0, abs(polygon_area(overlap)) / subject_area)


def rectangle(cx: float, cy: float, width: float, height: float) -> list[Point]:
    half_width = width / 2.0
    half_height = height / 2.0
    return [
        Point(cx - half_width, cy - half_height),
        Point(cx + half_width, cy - half_height),
        Point(cx + half_width, cy + half_height),
        Point(cx - half_width, cy + half_height),
    ]
