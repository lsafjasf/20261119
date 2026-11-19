"""End-to-end benchmark over gradient, noise and alpha test patterns."""

from __future__ import annotations

import os

from . import images
from .decode import decode_png
from .optimizer import (
    beam_search,
    heuristic_greedy,
    lookahead_search,
    precompute_variants,
)
from .png import UnsupportedInterlaceError, parse_png
from .report import build_and_measure, format_report


def _generate(out_dir: str, size: int) -> list[tuple[str, str]]:
    os.makedirs(out_dir, exist_ok=True)
    cases = []

    path = os.path.join(out_dir, "gradient_rgb8.png")
    images.write_png(path, size, size, 8, 2,
                     images.gradient_rgb(size, size))
    cases.append(("RGB8 gradient", path))

    path = os.path.join(out_dir, "noise_rgb8.png")
    images.write_png(path, size, size, 8, 2,
                     images.noise_rgb(size, size))
    cases.append(("RGB8 noise", path))

    path = os.path.join(out_dir, "alpha_rgba8.png")
    images.write_png(path, size, size, 8, 6,
                     images.alpha_pattern(size, size))
    cases.append(("RGBA8 alpha pattern", path))

    path = os.path.join(out_dir, "indexed4.png")
    rows, palette = images.indexed_pattern(size, size)
    images.write_png(path, size, size, 4, 3, rows, palette=palette)
    cases.append(("indexed 4-bit", path))

    path = os.path.join(out_dir, "gray16.png")
    images.write_png(path, size, size, 16, 0,
                     images.gradient_gray16(size, size))
    cases.append(("grayscale 16-bit", path))

    path = os.path.join(out_dir, "gray2.png")
    images.write_png(path, size, size, 2, 0,
                     images.sub_byte_gray(2, size, size))
    cases.append(("grayscale 2-bit", path))

    interlaced = os.path.join(out_dir, "interlaced_adam7.png")
    images.write_png(interlaced, 32, 32, 8, 2,
                     images.gradient_rgb(32, 32), interlace=1)
    return cases, interlaced


def run_benchmark(out_dir: str = "bench_out", size: int = 256, level: int = 9,
                  window: int = 4, beam_width: int = 8,
                  repeats: int = 20) -> str:
    cases, interlaced_path = _generate(out_dir, size)
    sections = []
    all_ok = True

    for label, path in cases:
        with open(path, "rb") as handle:
            original = handle.read()
        parsed = parse_png(original)
        _, variants = precompute_variants(parsed)
        results = [
            heuristic_greedy(parsed, level, variants=variants),
            lookahead_search(parsed, level, window, variants=variants),
            beam_search(parsed, level, beam_width, variants=variants),
        ]
        measurements = [
            build_and_measure(parsed, result, original, repeats=repeats)
            for result in results
        ]
        for item, result in zip(measurements, results):
            out_path = os.path.join(
                out_dir,
                f"{os.path.splitext(os.path.basename(path))[0]}"
                f".{result.strategy}.png")
            with open(out_path, "wb") as handle:
                handle.write(item["optimized"])
            if not item["verification"]["ok"]:
                all_ok = False
        sections.append(format_report(label, original, parsed, measurements))

    # Independent re-decode sanity check across every produced file.
    decode_checks = []
    for label, path in cases:
        stem = os.path.splitext(os.path.basename(path))[0]
        with open(path, "rb") as handle:
            source = decode_png(handle.read())
        for strategy in ("heuristic", "lookahead", "beam"):
            out_path = os.path.join(out_dir, f"{stem}.{strategy}.png")
            with open(out_path, "rb") as handle:
                decoded = decode_png(handle.read())
            same = decoded["pixels"] == source["pixels"]
            decode_checks.append((out_path, same))
    all_ok = all_ok and all(same for _, same in decode_checks)

    with open(interlaced_path, "rb") as handle:
        interlaced_bytes = handle.read()
    rejected = False
    try:
        parse_png(interlaced_bytes)
    except UnsupportedInterlaceError:
        rejected = True

    sections.append("== independent decode checks ==")
    sections.extend(
        f"{'OK ' if same else 'FAIL'} {path}" for path, same in decode_checks)
    sections.append(
        f"interlaced input rejected with explicit error: "
        f"{'OK' if rejected else 'FAIL'} ({interlaced_path})")
    sections.append(f"overall: {'ALL CHECKS PASSED' if all_ok and rejected
                               else 'FAILURES DETECTED'}")
    return "\n".join(sections)
