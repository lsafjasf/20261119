"""End-to-end demo: multi-level peak index over a 1-hour audio file."""
import os
import time
from array import array

from peakindex import PeakIndex
from peakindex.audio_gen import generate_wav, wav_data_offset

HERE = os.path.dirname(os.path.abspath(__file__))
WAV = os.path.join(HERE, "audio_1h.wav")
IDX = os.path.join(HERE, "peak_index")
SR = 44100
SECONDS = 3600
N = SR * SECONDS
IMPULSE_POS = 123_456_789          # single-sample +FS spike, unaligned
TRANSIENT_POS = 98_765_432         # 3-sample -FS transient
PIXELS = 1920                      # display width for zoom queries


def human(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024


def dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    return total


def step_generate():
    if os.path.exists(WAV):
        print("1. WAV exists, skipping generation: %s" % WAV)
        return
    print("1. generating 1-hour WAV ...")
    t0 = time.time()
    impulses = [(IMPULSE_POS, 32767)]
    impulses += [(TRANSIENT_POS + i, -32768) for i in range(3)]
    generate_wav(WAV, seconds=SECONDS, sample_rate=SR, impulses=impulses)
    print("   wrote %s (%s) in %.1fs"
          % (WAV, human(os.path.getsize(WAV)), time.time() - t0))


def step_build():
    print("2. building index ...")
    t0 = time.time()
    idx = PeakIndex.build(IDX, WAV, SR,
                          data_offset=wav_data_offset(WAV),
                          num_samples=N, factor=256, bins_per_chunk=4096)
    dt = time.time() - t0
    print("   built in %.1fs, index size %s (audio is %s)"
          % (dt, human(dir_size(IDX)), human(os.path.getsize(WAV))))
    for lv in idx.levels:
        print("   L%d: bin=%d samples (%.3fs), %d bins, %d chunks"
              % (lv.level, lv.bin_size, lv.bin_size / SR,
                 lv.num_bins, lv.num_chunks))
    return idx


def step_zoom_report(idx):
    print("3. zoom-on-demand reads (display width = %d px):" % PIXELS)
    print("   %-22s %-6s %-18s %8s %10s %8s"
          % ("window", "source", "bin size", "bins", "bytes read",
             "chunks"))
    windows = [
        ("full hour", 0, 3600),
        ("10 min", 1200.0, 1800.0),
        ("1 min", 2057.0, 2117.0),
        ("1 s", 2057.5, 2058.5),
        ("50 ms", 2057.55, 2057.60),
        ("5 ms", 2057.555, 2057.560),
        ("2000 samples", IMPULSE_POS / SR, (IMPULSE_POS + 2000) / SR),
    ]
    for label, a, b in windows:
        r = idx.query(a, b, PIXELS)
        bin_desc = ("1 sample" if r.bin_size == 1
                    else "%d samp (%.2f ms)"
                    % (r.bin_size, 1000 * r.bin_size / SR))
        print("   %-22s %-6s %-18s %8d %10s %8d"
              % (label, r.source, bin_desc, r.bins,
                 human(r.bytes_read), r.chunks_read))


def step_impulse_check(idx):
    print("4. single-sample impulse @%d (+32767) visibility:"
          % IMPULSE_POS)
    for lv in idx.levels:
        b = IMPULSE_POS // lv.bin_size
        _, maxs = idx.read_bins(lv.level, b, b + 1)
        ok = maxs[0] == 32767
        print("   L%d bin %d max=%d %s"
              % (lv.level, b, maxs[0], "OK" if ok else "LOST!"))
        assert ok
    r = idx.query(0, SECONDS, PIXELS)
    assert max(r.maxs) == 32767
    print("   full-hour overview query max=%d OK" % max(r.maxs))
    r = idx.query((IMPULSE_POS - 500) / SR, (IMPULSE_POS + 500) / SR,
                  PIXELS)
    assert r.mode == "samples" and 32767 in r.samples
    print("   sample-level zoom shows the real sample value OK")


def step_edit(idx):
    print("5. local edits and incremental invalidation:")
    edits = [
        ("quiet 0.5s burst (+-8000)", 100_000_000, 8000),
        ("full-scale 0.5s burst (+-32767)", 150_000_000, 32767),
    ]
    for label, start, amp in edits:
        seg = array("h", [amp if i % 2 else -amp for i in range(22050)])
        reports = idx.replace(start, seg)
        print("   %s @ sample %d:" % (label, start))
        for r in reports:
            print("     L%d: %d bins recomputed, %d changed, "
                  "%d chunk(s) rewritten, read %s, wrote %s"
                  % (r.level, r.bins_recomputed, r.bins_changed,
                     r.chunks_rewritten, human(r.bytes_read),
                     human(r.bytes_written)))
        touched = [r.level for r in reports]
        skipped = [lv.level for lv in idx.levels
                   if lv.level not in touched]
        print("     -> levels recomputed: %s; untouched: %s"
              % (touched, skipped or "none"))
    from peakindex.accel import minmax_reduce
    start = 150_000_000
    b0, b1 = start // 256, (start + 22050 - 1) // 256
    raw = idx.read_raw(b0 * 256, (b1 + 1) * 256)
    em, ex = minmax_reduce(raw, 256)
    am, ax = idx.read_bins(1, b0, b1 + 1)
    assert list(am) == list(em) and list(ax) == list(ex)
    print("   post-edit L1 bins match raw recomputation OK")


def main():
    step_generate()
    idx = step_build()
    step_zoom_report(idx)
    step_impulse_check(idx)
    step_edit(idx)
    idx2 = PeakIndex.open(IDX)
    r = idx2.query(0, SECONDS, PIXELS)
    print("6. reopened persisted index from disk; full-hour max=%d OK"
          % max(r.maxs))


if __name__ == "__main__":
    main()
