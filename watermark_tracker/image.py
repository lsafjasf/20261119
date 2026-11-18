"""Dependency-free grayscale image primitives."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

from .geometry import Point


def clamp_byte(value: float) -> int:
    return max(0, min(255, int(round(value))))


class GrayImage:
    def __init__(self, width: int, height: int, fill: int = 0):
        if width <= 0 or height <= 0:
            raise ValueError("image dimensions must be positive")
        self.width = width
        self.height = height
        self.data = bytearray([clamp_byte(fill)]) * (width * height)

    def copy(self) -> "GrayImage":
        result = GrayImage(self.width, self.height)
        result.data[:] = self.data
        return result

    def get(self, x: int, y: int) -> int:
        return self.data[y * self.width + x]

    def set(self, x: int, y: int, value: float) -> None:
        self.data[y * self.width + x] = clamp_byte(value)

    def sample_bilinear(self, x: float, y: float) -> float:
        if x < 0 or y < 0 or x > self.width - 1 or y > self.height - 1:
            return 0.0
        x0 = int(math.floor(x))
        y0 = int(math.floor(y))
        x1 = min(self.width - 1, x0 + 1)
        y1 = min(self.height - 1, y0 + 1)
        fx = x - x0
        fy = y - y0
        top = self.get(x0, y0) * (1.0 - fx) + self.get(x1, y0) * fx
        bottom = self.get(x0, y1) * (1.0 - fx) + self.get(x1, y1) * fx
        return top * (1.0 - fy) + bottom * fy

    def sample_average(
        self, x: float, y: float, radius: float = 0.0, samples: int = 3
    ) -> float:
        if radius <= 0 or samples <= 1:
            return self.sample_bilinear(x, y)
        step = 2.0 * radius / (samples - 1)
        values = [
            self.sample_bilinear(
                x - radius + column * step, y - radius + row * step
            )
            for row in range(samples)
            for column in range(samples)
        ]
        return sum(values) / len(values)

    def fill_polygon(
        self, points: Sequence[Point], value: float, supersample: int = 1
    ) -> None:
        if len(points) < 3:
            return
        min_x = max(0, int(math.floor(min(p.x for p in points))))
        max_x = min(self.width - 1, int(math.ceil(max(p.x for p in points))))
        min_y = max(0, int(math.floor(min(p.y for p in points))))
        max_y = min(self.height - 1, int(math.ceil(max(p.y for p in points))))
        samples = max(1, supersample)
        sample_count = float(samples * samples)
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                covered = 0
                for sy in range(samples):
                    py = y + (sy + 0.5) / samples
                    for sx in range(samples):
                        px = x + (sx + 0.5) / samples
                        if _point_in_polygon(px, py, points):
                            covered += 1
                if not covered:
                    continue
                coverage = covered / sample_count
                if coverage >= 0.999:
                    self.set(x, y, value)
                else:
                    previous = self.get(x, y)
                    self.set(x, y, previous * (1.0 - coverage) + value * coverage)

    def save_pgm(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            handle.write(f"P5\n{self.width} {self.height}\n255\n".encode("ascii"))
            handle.write(self.data)

    @classmethod
    def read_pgm(cls, path: str | Path) -> "GrayImage":
        payload = Path(path).read_bytes()
        if not payload.startswith(b"P5"):
            raise ValueError("only binary P5 PGM images are supported")
        offset = 2
        tokens: list[int] = []
        while len(tokens) < 3:
            while offset < len(payload) and chr(payload[offset]).isspace():
                offset += 1
            if offset < len(payload) and payload[offset : offset + 1] == b"#":
                while offset < len(payload) and payload[offset] not in (10, 13):
                    offset += 1
                continue
            start = offset
            while offset < len(payload) and chr(payload[offset]).isdigit():
                offset += 1
            if start == offset:
                raise ValueError("invalid PGM header")
            tokens.append(int(payload[start:offset]))
        offset += 1
        width, height, maximum = tokens
        if maximum != 255:
            raise ValueError("only 8-bit PGM images are supported")
        expected = width * height
        pixels = payload[offset : offset + expected]
        if len(pixels) != expected:
            raise ValueError("truncated PGM payload")
        image = cls(width, height)
        image.data[:] = pixels
        return image


def _point_in_polygon(x: float, y: float, points: Sequence[Point]) -> bool:
    inside = False
    previous = points[-1]
    for current in points:
        if (current.y > y) != (previous.y > y):
            crossing_x = (
                (previous.x - current.x) * (y - current.y)
                / (previous.y - current.y)
                + current.x
            )
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside


def otsu_threshold(image: GrayImage) -> int:
    histogram = [0] * 256
    for value in image.data:
        histogram[value] += 1
    total = len(image.data)
    weighted_sum = sum(index * count for index, count in enumerate(histogram))
    background_weight = 0
    background_sum = 0.0
    best_variance = -1.0
    best_threshold = 127
    for threshold in range(256):
        background_weight += histogram[threshold]
        if background_weight == 0:
            continue
        foreground_weight = total - background_weight
        if foreground_weight == 0:
            break
        background_sum += threshold * histogram[threshold]
        background_mean = background_sum / background_weight
        foreground_mean = (weighted_sum - background_sum) / foreground_weight
        variance = (
            background_weight
            * foreground_weight
            * (background_mean - foreground_mean) ** 2
        )
        if variance > best_variance:
            best_variance = variance
            best_threshold = threshold
    return best_threshold


def adjust_brightness(image: GrayImage, factor: float, offset: float = 0.0) -> None:
    for index, value in enumerate(image.data):
        image.data[index] = clamp_byte(value * factor + offset)


def add_noise(image: GrayImage, sigma: float, seed: int) -> None:
    if sigma <= 0:
        return
    state = seed & 0xFFFFFFFF
    for index in range(len(image.data)):
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        first = (state >> 8) / 16777216.0
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        second = (state >> 8) / 16777216.0
        gaussian = math.sqrt(-2.0 * math.log(max(first, 1e-9))) * math.cos(
            2.0 * math.pi * second
        )
        image.data[index] = clamp_byte(image.data[index] + sigma * gaussian)


def draw_rectangle(
    image: GrayImage,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    value: float,
    texture: float = 0.0,
    seed: int = 1,
) -> None:
    left = max(0, int(math.floor(min(x0, x1))))
    right = min(image.width - 1, int(math.ceil(max(x0, x1))))
    top = max(0, int(math.floor(min(y0, y1))))
    bottom = min(image.height - 1, int(math.ceil(max(y0, y1))))
    state = seed & 0xFFFFFFFF
    for y in range(top, bottom + 1):
        row_offset = y * image.width
        for x in range(left, right + 1):
            if texture:
                state = (1664525 * state + 1013904223) & 0xFFFFFFFF
                noise = ((state >> 16) / 65535.0 - 0.5) * texture
            else:
                noise = 0.0
            image.data[row_offset + x] = clamp_byte(value + noise)
