"""End-to-end demonstration of the partitioned fast-convolution library.

Run with:  python3 demo.py

It compares direct time-domain convolution against uniform and non-uniform
overlap-save, switches partition sizes, reports latency / MACs / memory,
exercises edge cases, and runs a dual-instance crossfaded filter switch.
Pure standard library; no third-party packages required.
"""

from __future__ import annotations

from partitioned_conv.crossfade import CrossfadeConvolver
from partitioned_conv.direct import DirectConvolver
from partitioned_conv.metrics import error_metrics, fmt_macs, latency_ms
from partitioned_conv.ols import NonUniformOLS, UniformOLS

SAMPLE_RATE = 48_000.0


def lcg(seed: int, n: int) -> list[float]:
    s = seed
    out = []
    for _ in range(n):
        s = (1103515245 * s + 12345) & 0x7FFFFFFF
        out.append(s / 0x7FFFFFFF - 0.5)
    return out


def heading(title: str) -> None:
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


def report(name: str, ref: list[float], out: list[float]) -> None:
    stats = error_metrics(ref, out)
    print(f"  {name:28s} len={len(out):4d}  "
          f"max|err|={stats['max_abs']:.2e}  SNR={stats['snr_db']:.1f} dB")


def main() -> None:
    h = lcg(101, 96)          # long FIR, deliberately not block-aligned
    x = lcg(202, 250)         # non-block-length input
    ref = DirectConvolver(h).process(x)

    heading("1. Correctness vs direct time-domain convolution (full tail)")
    print(f"  filter taps M={len(h)}, input N={len(x)}, "
          f"output N+M-1={len(ref)}")
    for block in (8, 16, 32, 64):
        conv = UniformOLS(h, block)
        out = conv.run(x)
        report(f"UniformOLS B={block}", ref, out)
    for block, tail in ((8, 16), (8, 32), (16, 64)):
        conv = NonUniformOLS(h, block, tail, front_taps=tail)
        out = conv.run(x)
        report(f"NonUniform B={block} C={tail}", ref, out)

    heading("2. Switching partition size: latency and MAC trade-off")
    direct_macs = len(x) * len(h)
    print(f"  direct convolution: {direct_macs:,} real MACs")
    for block in (16, 32, 64):
        conv = UniformOLS(h, block)
        conv.run(x)
        mem = conv.memory_slots()
        print(f"  B={block:3d}  latency={conv.latency_samples:3d} samples "
              f"({latency_ms(conv.latency_samples, SAMPLE_RATE):6.3f} ms)  "
              f"{fmt_macs(conv.complex_mac_count)}")
        print(f"         memory={mem['total_real_equiv']:,} real-equiv slots")
    nu = NonUniformOLS(h, 8, 64, front_taps=64)
    nu.run(x)
    cycle = nu.macs_per_full_cycle()
    print(f"  NonUniform B=8 C=64 latency={nu.latency_samples} samples "
          f"({latency_ms(nu.latency_samples, SAMPLE_RATE):.3f} ms)")
    print(f"         MACs per {64}-sample cycle: "
          f"front={cycle['front_per_cycle']:,} tail={cycle['tail_per_cycle']:,}")
    print(f"         total complex MACs={nu.complex_mac_count:,}  "
          f"memory={nu.memory_slots()['total_real_equiv']:,} slots")

    heading("3. Boundary cases: impulse, partial block, empty input")
    impulse_in = [1.0] + [0.0] * 31
    impulse_ref = DirectConvolver(h).process(impulse_in)
    impulse_out = UniformOLS(h, 16).run(impulse_in)
    report("unit impulse (recovers filter)", impulse_ref, impulse_out)
    partial = lcg(303, 13)  # 13 samples, not a multiple of any block
    partial_ref = DirectConvolver(h).process(partial)
    report("partial-block input", partial_ref,
           NonUniformOLS(h, 8, 32).run(partial))
    empty_out = UniformOLS(h, 16).run([])
    print(f"  empty input -> {len(empty_out)} samples "
          f"({'OK' if empty_out == [] else 'FAIL'})")
    short_x = lcg(404, 2)
    report("filter longer than input",
           DirectConvolver(h).process(short_x),
           UniformOLS(h, 8).run(short_x))

    heading("4. Dual-instance crossfaded filter replacement")
    h2 = lcg(505, len(h))
    fade = 32
    switch_at = 80

    def factory(impulse):
        return NonUniformOLS(impulse, 8, 32, front_taps=32)

    xf = CrossfadeConvolver(factory, h, fade, gain="linear")
    xf.process(x[:switch_at])
    xf.switch(h2)
    xf.process(x[switch_at:])
    xf.flush()
    output = xf.output[: len(x) + len(h) - 1]

    old_full = DirectConvolver(h).process(x)
    new_full = DirectConvolver(h2).process(x)
    stats = xf.transition_region_error(old_full, new_full)
    print(f"  switch at sample {switch_at}, fade window {fade} samples")
    print(f"  extra memory while both live: "
          f"{xf.extra_memory_peak:,} real-equiv slots")
    print(f"  transition region ({stats['samples']} samples): "
          f"max|err|={stats['max_abs']:.2e} rms={stats['rms']:.2e}")

    # After the fade plus one filter length, response equals new filter alone.
    settled = xf.switch_sample + fade + len(h2)
    settled_err = max(
        abs(output[i] - new_full[i]) for i in range(settled, len(output)))
    print(f"  post-settle deviation from new filter: {settled_err:.2e}")
    print("  crossfade " + ("OK" if stats["max_abs"] < 1e-6
                            and settled_err < 1e-8 else "FAIL"))


if __name__ == "__main__":
    main()
