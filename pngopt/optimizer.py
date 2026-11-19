"""Scanline filter selection strategies.

Three strategies are provided:

* ``heuristic`` -- greedy per-row estimate minimizing sum-of-absolute-bytes
  (the classic fast heuristic; no compression feedback);
* ``lookahead`` -- rolling finite lookahead window where every filter sequence
  inside the window is scored with a real zlib stream (via
  ``compressobj.copy``);
* ``beam`` -- fixed-width beam search over true compressed payload size, also
  driven by real zlib streams.
"""

from __future__ import annotations

from dataclasses import dataclass

import zlib

from .png import (
    FILTER_NAMES,
    FILTER_NONE,
    ParsedPNG,
    encode_row,
    reconstruct_row,
)

METHODS = tuple(range(5))


@dataclass
class StrategyResult:
    strategy: str
    filters: tuple          # chosen filter type per scanline
    payload: bytes          # compressed zlib stream written into IDAT
    stats: dict             # usage counts/rates plus payload and stream sizes


def _raw_rows(parsed: ParsedPNG) -> list[bytes]:
    rows = []
    prev = None
    for filtered in parsed.rows:
        prev = reconstruct_row(filtered, prev, parsed.row_stride, parsed.bpp)
        rows.append(prev)
    return rows


def precompute_variants(parsed: ParsedPNG):
    """All five filtered variants for every scanline, indexed [row][method]."""
    raw_rows = _raw_rows(parsed)
    variants = []
    prev = None
    for raw in raw_rows:
        variants.append([
            encode_row(raw, prev, parsed.row_stride, parsed.bpp, method)
            for method in METHODS])
        prev = raw
    return raw_rows, variants


def _sad(row: bytes) -> int:
    # Skip the filter-type byte when scoring residual magnitude.
    return sum(row[1:])


def _usage_stats(filters) -> dict:
    counts = {name: 0 for name in FILTER_NAMES}
    for method in filters:
        counts[FILTER_NAMES[method]] += 1
    total = len(filters)
    rates = {name: (count / total if total else 0.0)
             for name, count in counts.items()}
    return {"counts": counts, "rates": rates}


def _deflate(stream: bytes, level: int) -> bytes:
    compressor = zlib.compressobj(level, zlib.DEFLATED, 15)
    return compressor.compress(stream) + compressor.flush()


def _result(strategy: str, parsed: ParsedPNG, variants, chosen,
            level: int) -> StrategyResult:
    filtered_stream = b"".join(variants[r][chosen[r]]
                               for r in range(parsed.height))
    payload = _deflate(filtered_stream, level)
    stats = _usage_stats(chosen)
    stats["idat_compressed_bytes"] = len(payload)
    stats["filtered_stream_bytes"] = len(filtered_stream)
    return StrategyResult(strategy, tuple(chosen), payload, stats)


def heuristic_greedy(parsed: ParsedPNG, level: int = 9,
                     variants=None) -> StrategyResult:
    """Pick, per row, the filter with the smallest sum-of-absolutes estimate."""
    if variants is None:
        _, variants = precompute_variants(parsed)
    chosen = []
    for row_variants in variants:
        best = FILTER_NONE
        best_score = None
        for method in METHODS:
            score = _sad(row_variants[method])
            if best_score is None or score < best_score:
                best_score = score
                best = method
        chosen.append(best)
    return _result("heuristic", parsed, variants, chosen, level)


def lookahead_search(parsed: ParsedPNG, level: int = 9, window: int = 4,
                     variants=None) -> StrategyResult:
    """Rolling lookahead over real zlib size.

    At each step every 5**min(window, remaining) filter combination is tried
    on a copied compressor; the best first filter is committed and the window
    advances one row.  Each branch is drained with ``Z_SYNC_FLUSH`` so its
    measured size includes data still held in deflate's internal buffer.
    ``window=1`` collapses to greedy true-size search.
    """
    if window < 1:
        raise ValueError("lookahead window must be >= 1")
    if variants is None:
        _, variants = precompute_variants(parsed)

    chosen = []
    height = parsed.height
    row = 0
    base = zlib.compressobj(level, zlib.DEFLATED, 15)

    while row < height:
        depth = min(window, height - row)
        best_first = FILTER_NONE
        best_size = None

        def explore(start_row: int, co, prefix: tuple[int, ...],
                    accumulated: int) -> int:
            nonlocal best_first, best_size
            if len(prefix) == depth:
                return accumulated
            local_best = None
            for method in METHODS:
                branch = co.copy()
                block = variants[start_row][method]
                produced = len(branch.compress(block))
                drained = len(branch.flush(zlib.Z_SYNC_FLUSH))
                total = explore(start_row + 1, branch, prefix + (method,),
                                accumulated + produced + drained)
                if local_best is None or total < local_best:
                    local_best = total
                    if len(prefix) == 0:
                        best_first = method
                        best_size = total
            return local_best

        explore(row, base, (), 0)
        chosen.append(best_first)
        base.compress(variants[row][best_first])
        # Keep the shared history compressor small so copying it per branch
        # stays cheap; the emitted IDAT payload is re-deflated separately.
        base.flush(zlib.Z_SYNC_FLUSH)
        row += 1
        assert best_size is not None

    return _result("lookahead", parsed, variants, chosen, level)


def beam_search(parsed: ParsedPNG, level: int = 9, width: int = 8,
                variants=None) -> StrategyResult:
    """Beam search retaining the ``width`` smallest true compressed prefixes.

    Prefix sizes are measured after a ``Z_SYNC_FLUSH`` drain so deflate's
    internal buffering cannot bias comparisons toward short outputs.
    """
    if width < 1:
        raise ValueError("beam width must be >= 1")
    if variants is None:
        _, variants = precompute_variants(parsed)

    # Each beam state: (consumed compressed bytes, compressor, filters).
    beams = [(0, zlib.compressobj(level, zlib.DEFLATED, 15), [])]
    for row_variants in variants:
        candidates = []
        for size, co, filters in beams:
            for method in METHODS:
                branch = co.copy()
                produced = len(branch.compress(row_variants[method]))
                drained = len(branch.flush(zlib.Z_SYNC_FLUSH))
                candidates.append(
                    (size + produced + drained, branch, filters + [method]))
        candidates.sort(key=lambda item: (item[0], item[2]))
        beams = candidates[:width]

    finalized = []
    for size, co, filters in beams:
        total = size + len(co.flush())
        finalized.append((total, filters))
    finalized.sort(key=lambda item: (item[0], item[1]))
    chosen = finalized[0][1]
    return _result("beam", parsed, variants, chosen, level)


def original_stream_stats(parsed: ParsedPNG) -> dict:
    """Stats describing the source file's existing filter choices."""
    chosen = [row[0] for row in parsed.rows]
    stats = _usage_stats(chosen)
    idat = b"".join(body for ctype, body in parsed.chunks
                    if ctype == b"IDAT")
    stats["idat_compressed_bytes"] = len(idat)
    stats["filtered_stream_bytes"] = len(parsed.raw)
    return stats


STRATEGIES = {
    "heuristic": heuristic_greedy,
    "lookahead": lookahead_search,
    "beam": beam_search,
}
