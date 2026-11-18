"""Multi-level chunked min/max peak index with disk persistence.

Layout on disk:
    <index_dir>/manifest.json
    <index_dir>/L01/000000.bin ...   (level 1 bins, interleaved int16 min,max)
    <index_dir>/L02/...

Level k bin covers factor**k source samples. Because every level stores
exact min/max of its children, a single-sample impulse is preserved at
every overview level. Local edits only rewrite the chunks whose bins
cover the edited range, and propagation stops at the first level whose
bins are unchanged.
"""
import json
import os
from array import array
from dataclasses import dataclass, field

from .accel import minmax_reduce

FORMAT_VERSION = 1
BYTES_PER_BIN = 4  # int16 min + int16 max


@dataclass
class LevelInfo:
    level: int
    bin_size: int      # source samples covered by one bin
    num_bins: int
    num_chunks: int


@dataclass
class QueryResult:
    source: str            # "raw" or "L<k>"
    level: int             # 0 = raw samples
    bin_size: int          # source samples per returned bin (1 for raw)
    start_sample: int      # source sample index of first returned bin
    mode: str              # "samples" | "minmax"
    mins: array = None
    maxs: array = None
    samples: array = None  # only when mode == "samples"
    bytes_read: int = 0
    chunks_read: int = 0

    @property
    def bins(self):
        if self.mode == "samples":
            return len(self.samples)
        return len(self.mins)


@dataclass
class LevelInvalidation:
    level: int
    bins_recomputed: int
    bins_changed: int
    chunks_rewritten: int
    bytes_read: int
    bytes_written: int


class _Counter:
    def __init__(self):
        self.bytes_read = 0
        self.bytes_written = 0
        self.chunks = 0
        self.chunks_written = 0


def _chunk_name(idx):
    return "%06d.bin" % idx


class PeakIndex:
    def __init__(self, index_dir, manifest):
        self.dir = index_dir
        m = manifest
        self.source_path = os.path.join(index_dir, m["source"])
        self.data_offset = m["data_offset"]
        self.sample_rate = m["sample_rate"]
        self.num_samples = m["num_samples"]
        self.factor = m["factor"]
        self.bins_per_chunk = m["bins_per_chunk"]
        self.levels = [LevelInfo(**lv) for lv in m["levels"]]

    # ---------------------------------------------------------- build
    @classmethod
    def build(cls, index_dir, source_path, sample_rate, data_offset=0,
              num_samples=None, factor=256, bins_per_chunk=4096,
              progress=None):
        """Build a persisted multi-level index over int16 mono samples."""
        os.makedirs(index_dir, exist_ok=True)
        if num_samples is None:
            num_samples = (os.path.getsize(source_path) - data_offset) // 2
        src = os.open(source_path, os.O_RDONLY)
        try:
            levels = []
            # level 1 from raw samples, one chunk of bins per read block
            block = factor * bins_per_chunk
            num_bins = (num_samples + factor - 1) // factor
            num_chunks = (num_bins + bins_per_chunk - 1) // bins_per_chunk
            lvl_dir = os.path.join(index_dir, "L01")
            os.makedirs(lvl_dir, exist_ok=True)
            for c in range(num_chunks):
                s0 = c * block
                s1 = min(num_samples, s0 + block)
                raw = _pread_array(src, data_offset + 2 * s0, s1 - s0)
                mins, maxs = minmax_reduce(raw, factor)
                _write_chunk(lvl_dir, c, mins, maxs)
                if progress:
                    progress(1, c + 1, num_chunks)
            levels.append(LevelInfo(1, factor, num_bins, num_chunks))
            # higher levels from previous level bins (small: fits in RAM)
            prev = levels[0]
            while prev.num_bins > 1 and prev.level < 32:
                k = prev.level + 1
                flat = cls._read_level_flat_static(index_dir, prev,
                                                   bins_per_chunk)
                mins, maxs = minmax_reduce(flat, 2 * factor)
                num_bins = len(mins)
                num_chunks = (num_bins + bins_per_chunk - 1) // bins_per_chunk
                lvl_dir = os.path.join(index_dir, "L%02d" % k)
                os.makedirs(lvl_dir, exist_ok=True)
                for c in range(num_chunks):
                    b0 = c * bins_per_chunk
                    b1 = min(num_bins, b0 + bins_per_chunk)
                    _write_chunk(lvl_dir, c, mins[b0:b1], maxs[b0:b1])
                cur = LevelInfo(k, prev.bin_size * factor, num_bins,
                                num_chunks)
                levels.append(cur)
                prev = cur
        finally:
            os.close(src)
        manifest = {
            "format": FORMAT_VERSION,
            "source": os.path.abspath(source_path),
            "data_offset": data_offset,
            "sample_rate": sample_rate,
            "num_samples": num_samples,
            "factor": factor,
            "bins_per_chunk": bins_per_chunk,
            "levels": [lv.__dict__ for lv in levels],
        }
        with open(os.path.join(index_dir, "manifest.json"), "w") as fh:
            json.dump(manifest, fh, indent=2)
        return cls(index_dir, manifest)

    @classmethod
    def open(cls, index_dir):
        with open(os.path.join(index_dir, "manifest.json")) as fh:
            return cls(index_dir, json.load(fh))

    # ------------------------------------------------------- io helpers
    def _chunk_path(self, level, chunk_idx):
        return os.path.join(self.dir, "L%02d" % level,
                            _chunk_name(chunk_idx))

    @staticmethod
    def _read_level_flat_static(index_dir, level_info, bins_per_chunk):
        flat = array("h")
        lvl_dir = os.path.join(index_dir, "L%02d" % level_info.level)
        for c in range(level_info.num_chunks):
            with open(os.path.join(lvl_dir, _chunk_name(c)), "rb") as fh:
                flat.frombytes(fh.read())
        return flat

    def read_bins(self, level, b0, b1, counter=None):
        """Read bins [b0, b1) of a level. Returns (mins, maxs)."""
        info = self.levels[level - 1]
        b1 = min(b1, info.num_bins)
        mins, maxs = array("h"), array("h")
        if b0 >= b1:
            return mins, maxs
        c0 = b0 // self.bins_per_chunk
        c1 = (b1 - 1) // self.bins_per_chunk
        for c in range(c0, c1 + 1):
            lo = b0 - c * self.bins_per_chunk if c == c0 else 0
            hi = (b1 - c * self.bins_per_chunk if c == c1
                  else self.bins_per_chunk)
            hi = min(hi, info.num_bins - c * self.bins_per_chunk)
            with open(self._chunk_path(level, c), "rb") as fh:
                fh.seek(lo * BYTES_PER_BIN)
                data = fh.read((hi - lo) * BYTES_PER_BIN)
            if counter is not None:
                counter.bytes_read += len(data)
                counter.chunks += 1
            flat = array("h")
            flat.frombytes(data)
            mins.extend(flat[0::2])
            maxs.extend(flat[1::2])
        return mins, maxs

    def read_raw(self, s0, s1, counter=None):
        s1 = min(s1, self.num_samples)
        fd = os.open(self.source_path, os.O_RDONLY)
        try:
            data = os.pread(fd, 2 * (s1 - s0), self.data_offset + 2 * s0)
        finally:
            os.close(fd)
        if counter is not None:
            counter.bytes_read += len(data)
            counter.chunks += 1
        out = array("h")
        out.frombytes(data)
        return out

    # ---------------------------------------------------------- query
    def pick_level(self, span, max_bins, read_cap=4):
        """Choose the level to serve `span` samples into <= ~max_bins bins.

        Returns level 0 for raw samples when the zoom is deep enough that
        real samples can be shown. Otherwise the finest level whose bin
        size is <= span/max_bins, stepping coarser while the read would
        exceed read_cap * max_bins bins.
        """
        ideal = span / max(1, max_bins)
        if ideal <= self.factor:
            return 0
        choice = self.levels[0]
        for lv in self.levels:
            if lv.bin_size <= ideal:
                choice = lv
            else:
                break
        while (choice.level < self.levels[-1].level
               and span // choice.bin_size > read_cap * max_bins):
            choice = self.levels[choice.level]  # next coarser level
        return choice.level

    def query(self, start_s, end_s, max_bins):
        """On-demand read for a zoom window. Returns a QueryResult."""
        s0 = max(0, int(start_s * self.sample_rate))
        s1 = min(self.num_samples, int(end_s * self.sample_rate))
        if s1 <= s0:
            raise ValueError("empty query range")
        span = s1 - s0
        counter = _Counter()
        level = self.pick_level(span, max_bins)
        if level == 0:
            raw = self.read_raw(s0, s1, counter)
            if span <= max_bins:
                return QueryResult("raw", 0, 1, s0, "samples",
                                   samples=raw,
                                   bytes_read=counter.bytes_read,
                                   chunks_read=counter.chunks)
            group = (span + max_bins - 1) // max_bins
            mins, maxs = minmax_reduce(raw, group)
            return QueryResult("raw", 0, group, s0, "minmax",
                               mins=mins, maxs=maxs,
                               bytes_read=counter.bytes_read,
                               chunks_read=counter.chunks)
        info = self.levels[level - 1]
        b0 = s0 // info.bin_size
        b1 = (s1 - 1) // info.bin_size + 1
        mins, maxs = self.read_bins(level, b0, b1, counter)
        return QueryResult("L%d" % level, level, info.bin_size,
                           b0 * info.bin_size, "minmax",
                           mins=mins, maxs=maxs,
                           bytes_read=counter.bytes_read,
                           chunks_read=counter.chunks)

    # --------------------------------------------------------- editing
    def replace(self, start_sample, samples):
        """Overwrite samples [start, start+len) and invalidate only the
        index bins that cover the edited range, propagating upwards only
        while parent bins actually change. Returns per-level reports."""
        samples = array("h", samples)
        start = int(start_sample)
        end = start + len(samples)
        if start < 0 or end > self.num_samples:
            raise ValueError("replace range out of bounds")
        fd = os.open(self.source_path, os.O_WRONLY)
        try:
            os.pwrite(fd, samples.tobytes(), self.data_offset + 2 * start)
        finally:
            os.close(fd)
        reports = []
        f = self.factor
        b0, b1 = start // f, (end - 1) // f  # inclusive bin range, L1
        # level 1: recompute affected bins from raw samples
        counter = _Counter()
        raw = self.read_raw(b0 * f, min(self.num_samples, (b1 + 1) * f),
                            counter)
        mins, maxs = minmax_reduce(raw, f)
        changed = self._write_bin_range(1, b0, mins, maxs, counter)
        reports.append(LevelInvalidation(
            1, len(mins), changed, counter.chunks_written,
            counter.bytes_read, counter.bytes_written))
        # higher levels: recompute from children, stop when stable
        lo, hi = b0, b1
        k = 2
        while changed and k <= len(self.levels):
            p0, p1 = lo // f, hi // f
            counter = _Counter()
            cmins, cmaxs = self.read_bins(k - 1, p0 * f, (p1 + 1) * f,
                                          counter)
            flat = array("h")
            for mn, mx in zip(cmins, cmaxs):
                flat.append(mn)
                flat.append(mx)
            pmins, pmaxs = minmax_reduce(flat, 2 * f)
            changed = self._write_bin_range(k, p0, pmins, pmaxs, counter)
            reports.append(LevelInvalidation(
                k, len(pmins), changed, counter.chunks_written,
                counter.bytes_read, counter.bytes_written))
            lo, hi = p0, p1
            k += 1
        return reports

    def _write_bin_range(self, level, b0, mins, maxs, counter):
        """Write bins starting at b0 into level chunks; returns how many
        bin values actually changed."""
        info = self.levels[level - 1]
        changed = 0
        i = 0
        n = len(mins)
        while i < n:
            b = b0 + i
            c = b // self.bins_per_chunk
            local = b - c * self.bins_per_chunk
            take = min(n - i, self.bins_per_chunk - local,
                       info.num_bins - c * self.bins_per_chunk)
            path = self._chunk_path(level, c)
            with open(path, "rb") as fh:
                old = array("h")
                old.frombytes(fh.read())
            counter.bytes_read += len(old) * 2
            counter.chunks += 1
            new = old[:]
            for j in range(take):
                mn, mx = mins[i + j], maxs[i + j]
                pos = 2 * (local + j)
                if new[pos] != mn or new[pos + 1] != mx:
                    changed += 1
                new[pos] = mn
                new[pos + 1] = mx
            if new != old:
                lo = 2 * local
                hi = 2 * (local + take)
                fd = os.open(path, os.O_WRONLY)
                try:
                    os.pwrite(fd, new[lo:hi].tobytes(),
                              lo * 2)
                finally:
                    os.close(fd)
                counter.bytes_written += (hi - lo) * 2
                counter.chunks_written += 1
            i += take
        return changed

def _pread_array(fd, offset, count):
    out = array("h")
    out.frombytes(os.pread(fd, 2 * count, offset))
    return out


def _write_chunk(lvl_dir, chunk_idx, mins, maxs):
    flat = array("h")
    for mn, mx in zip(mins, maxs):
        flat.append(mn)
        flat.append(mx)
    tmp = os.path.join(lvl_dir, _chunk_name(chunk_idx) + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(flat.tobytes())
    os.replace(tmp, os.path.join(lvl_dir, _chunk_name(chunk_idx)))
