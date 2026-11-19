"""Round-trip verification and human-readable reporting."""

from __future__ import annotations

from .decode import decode_png, time_decode
from .optimizer import original_stream_stats
from .png import FILTER_NAMES, ParsedPNG, build_png, parse_png


def verify_output(original: ParsedPNG, optimized_bytes: bytes) -> dict:
    """Re-decode optimized bytes from scratch and compare against the source.

    Checks performed:
      * independent decode succeeds (own chunk/CRC parsing and unfiltering);
      * reconstructed pixels are byte-identical, row by row;
      * every non-IDAT/non-IEND chunk (type + body) is preserved exactly;
      * IHDR bytes, hence width/height/bit depth/color type, are unchanged.
    """
    reparsed = parse_png(optimized_bytes)
    decoded = decode_png(optimized_bytes)

    source_pixels = []
    prev = None
    for filtered in original.rows:
        from .png import reconstruct_row
        prev = reconstruct_row(filtered, prev, original.row_stride,
                               original.bpp)
        source_pixels.append(prev)
    source_pixels = b"".join(source_pixels)

    mismatches = sum(
        a != b for a, b in zip(source_pixels, decoded["pixels"]))
    pixel_identical = (
        len(source_pixels) == len(decoded["pixels"]) and mismatches == 0)

    kept = [(t, b) for t, b in original.chunks
            if t not in (b"IDAT", b"IEND")]
    new_kept = [(t, b) for t, b in reparsed.chunks
                if t not in (b"IDAT", b"IEND")]
    chunks_identical = kept == new_kept
    ihdr_identical = original.chunks[0][1] == reparsed.chunks[0][1]

    return {
        "pixel_identical": pixel_identical,
        "pixel_mismatches": mismatches,
        "ancillary_chunks_identical": chunks_identical,
        "ihdr_identical": ihdr_identical,
        "ok": pixel_identical and chunks_identical and ihdr_identical,
    }


def _percent(part: int, whole: int) -> str:
    if whole == 0:
        return "0.0%"
    return f"{100.0 * part / whole:5.1f}%"


def format_usage(stats: dict) -> str:
    counts = stats["counts"]
    total = sum(counts.values())
    return "  ".join(
        f"{name}={counts[name]} ({_percent(counts[name], total).strip()})"
        for name in FILTER_NAMES)


def build_and_measure(parsed: ParsedPNG, result, original_bytes: bytes,
                      repeats: int = 20) -> dict:
    optimized = build_png(parsed, result.payload)
    verification = verify_output(parsed, optimized)
    mean_s, best_s, _ = time_decode(optimized, repeats=repeats)
    source_mean, source_best, _ = time_decode(original_bytes, repeats=repeats)
    return {
        "strategy": result.strategy,
        "filters": result.filters,
        "stats": result.stats,
        "output_bytes": len(optimized),
        "optimized": optimized,
        "verification": verification,
        "decode_mean_s": mean_s,
        "decode_best_s": best_s,
        "source_decode_mean_s": source_mean,
        "source_decode_best_s": source_best,
    }


def format_report(label: str, original_bytes: bytes, parsed: ParsedPNG,
                  measurements: list[dict]) -> str:
    lines = []
    src_stats = original_stream_stats(parsed)
    lines.append(f"== {label} ==")
    lines.append(
        f"image {parsed.width}x{parsed.height} depth={parsed.bit_depth} "
        f"color_type={parsed.color_type} rows={parsed.height}")
    lines.append(
        f"source file={len(original_bytes)} B "
        f"IDAT={src_stats['idat_compressed_bytes']} B "
        f"decode(mean/best)="
        f"{measurements[0]['source_decode_mean_s'] * 1e3:.3f}/"
        f"{measurements[0]['source_decode_best_s'] * 1e3:.3f} ms")
    lines.append("source filters: " + format_usage(src_stats))
    header = (f"{'strategy':<10} {'file B':>9} {'IDAT B':>9} "
              f"{'vs src':>8} {'decode ms':>10} {'verdict':>8}")
    lines.append(header)
    for item in measurements:
        delta = item["output_bytes"] - len(original_bytes)
        verdict = "OK" if item["verification"]["ok"] else "FAIL"
        lines.append(
            f"{item['strategy']:<10} "
            f"{item['output_bytes']:>9} "
            f"{item['stats']['idat_compressed_bytes']:>9} "
            f"{delta:>+8} "
            f"{item['decode_mean_s'] * 1e3:>7.3f} "
            f"{verdict:>8}")
        lines.append("  filters: " + format_usage(item["stats"]))
    return "\n".join(lines)
