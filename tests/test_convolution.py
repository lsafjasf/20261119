import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from partitioned_conv.crossfade import CrossfadeConvolver
from partitioned_conv.direct import DirectConvolver
from partitioned_conv.fft import dft_naive, fft, ifft, is_power_of_two
from partitioned_conv.metrics import error_metrics
from partitioned_conv.ols import NonUniformOLS, UniformOLS


def rng(seed: int):
    state = {"s": seed}

    def nxt():
        # Tiny deterministic LCG, no numpy dependency.
        state["s"] = (1103515245 * state["s"] + 12345) & 0x7FFFFFFF
        return state["s"] / 0x7FFFFFFF - 0.5

    return nxt


def assert_close(testcase, ref, out, tol=1e-8):
    testcase.assertEqual(len(ref), len(out))
    worst = max((abs(a - b) for a, b in zip(ref, out)), default=0.0)
    testcase.assertLess(worst, tol, f"worst abs error {worst}")


class FFTTests(unittest.TestCase):
    def test_fft_matches_naive_dft(self):
        rand = rng(1)
        for n in (1, 2, 4, 8, 16, 32):
            x = [complex(rand(), rand()) for _ in range(n)]
            ref = dft_naive(x)
            got = fft(x)
            for a, b in zip(ref, got):
                self.assertAlmostEqual(abs(a - b), 0.0, places=9)

    def test_ifft_round_trip(self):
        rand = rng(2)
        for n in (2, 8, 64):
            x = [complex(rand(), rand()) for _ in range(n)]
            y = ifft(fft(x))
            for a, b in zip(x, y):
                self.assertAlmostEqual(abs(a - b), 0.0, places=9)


class UniformOLSTests(unittest.TestCase):
    def test_impulse_response_is_recovered(self):
        # Unit impulse input -> output equals the filter itself.
        h = [0.25, -0.5, 0.75, -0.1, 0.3, 0.0, 0.2]
        for b in (2, 4, 8):
            conv = UniformOLS(h, b)
            out = conv.run([1.0] + [0.0] * 5)
            assert_close(self, h, out[: len(h)], tol=1e-10)

    def test_full_output_and_tail_match_direct(self):
        rand = rng(3)
        h = [rand() for _ in range(19)]
        x = [rand() for _ in range(53)]  # not a multiple of any block
        ref = DirectConvolver(h).process(x)
        for b in (2, 4, 8, 16):
            conv = UniformOLS(h, b)
            out = conv.run(x)
            self.assertEqual(len(out), len(x) + len(h) - 1)
            assert_close(self, ref, out, tol=1e-8)

    def test_streaming_chunks_equal_one_shot(self):
        rand = rng(4)
        h = [rand() for _ in range(11)]
        x = [rand() for _ in range(40)]
        one_shot = UniformOLS(h, 4).run(x)
        conv = UniformOLS(h, 4)
        streamed = []
        for chunk in (x[:7], x[7:15], x[15:31], x[31:]):
            streamed.extend(conv.process(chunk))
        streamed.extend(conv.flush())
        assert_close(self, one_shot, streamed, tol=1e-12)

    def test_empty_input(self):
        h = [1.0, 0.0, -1.0]
        conv = UniformOLS(h, 4)
        out = conv.run([])
        self.assertEqual(out, [])
        # Streaming empty chunks before real data must not change results.
        conv2 = UniformOLS(h, 4)
        self.assertEqual(conv2.process([]), [])
        y = conv2.process([1.0, 2.0])
        y += conv2.flush()
        assert_close(self, DirectConvolver(h).process([1.0, 2.0]), y, 1e-10)

    def test_filter_longer_than_input(self):
        rand = rng(5)
        h = [rand() for _ in range(40)]
        x = [rand() for _ in range(3)]
        ref = DirectConvolver(h).process(x)
        assert_close(self, ref, UniformOLS(h, 8).run(x), 1e-8)


class NonUniformOLSTests(unittest.TestCase):
    def test_matches_direct_across_partition_shapes(self):
        rand = rng(6)
        h = [rand() for _ in range(37)]
        x = [rand() for _ in range(71)]
        ref = DirectConvolver(h).process(x)
        shapes = [(2, 4), (4, 8), (4, 16), (8, 16), (8, 32)]
        for b, c in shapes:
            conv = NonUniformOLS(h, b, c, front_taps=c)
            out = conv.run(x)
            self.assertEqual(len(out), len(x) + len(h) - 1)
            assert_close(self, ref, out, 1e-8)

    def test_impulse_and_empty_edges(self):
        h = [0.1 * k for k in range(1, 25)]
        out = NonUniformOLS(h, 4, 8).run([1.0] + [0.0] * 3)
        assert_close(self, h, out[: len(h)], 1e-10)
        self.assertEqual(NonUniformOLS(h, 4, 8).run([]), [])

    def test_front_only_filter_still_works(self):
        h = [1.0, -1.0, 0.5]
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        ref = DirectConvolver(h).process(x)
        out = NonUniformOLS(h, 4, 8).run(x)
        assert_close(self, ref, out, 1e-10)

    def test_odd_chunk_streaming_has_no_provisional_samples(self):
        # Regression: provisional partial sums near the input end must not
        # leak out of process(); only flush() finalises the ring-down.
        rand = rng(9)
        h = [rand() for _ in range(33)]
        x = [rand() for _ in range(100)]
        ref = DirectConvolver(h).process(x)
        conv = NonUniformOLS(h, 4, 16, front_taps=16)
        out: list[float] = []
        i = 0
        for step in (3, 7, 1, 15, 4, 30, 2):
            out.extend(conv.process(x[i:i + step]))
            i += step
        out.extend(conv.process(x[i:]))
        out.extend(conv.flush())
        assert_close(self, ref, out, 1e-12)


class CrossfadeTests(unittest.TestCase):
    def _factory(self, block, tail):
        def make(h):
            return NonUniformOLS(h, block, tail, front_taps=tail)
        return make

    def test_dual_instance_tracks_ideal_crossfade(self):
        rand = rng(7)
        h1 = [rand() for _ in range(20)]
        h2 = [rand() for _ in range(20)]
        x = [rand() for _ in range(120)]
        switch_at, fade = 40, 16

        old_full = DirectConvolver(h1).process(x)
        new_full = DirectConvolver(h2).process(x)

        xf = CrossfadeConvolver(self._factory(4, 8), h1, fade)
        xf.process(x[:switch_at])
        xf.switch(h2)
        xf.process(x[switch_at:])
        xf.flush()

        stats = xf.transition_region_error(old_full, new_full)
        self.assertLess(stats["max_abs"], 1e-7)
        self.assertGreater(xf.extra_memory_peak, 0)

    def test_switching_then_settles_to_new_filter(self):
        rand = rng(8)
        h1 = [rand() for _ in range(12)]
        h2 = [rand() for _ in range(12)]
        x = [rand() for _ in range(96)]
        fade = 8

        xf = CrossfadeConvolver(self._factory(2, 4), h1, fade)
        xf.process(x[:24])
        xf.switch(h2)
        xf.process(x[24:])
        xf.flush()

        output = xf._output[: len(x) + len(h2) - 1]
        new_full = DirectConvolver(h2).process(x)
        # After the fade window plus one full filter length of memory,
        # the output must be exactly the new filter's constant response.
        settled = xf.switch_sample + fade + len(h2)
        tail = output[settled:]
        assert_close(self, new_full[settled:len(output)], tail, 1e-8)

    def test_equal_power_fade_and_validation(self):
        from partitioned_conv.crossfade import equal_power_gain
        old_g, new_g = equal_power_gain(16)
        for a, b in zip(old_g, new_g):
            self.assertAlmostEqual(a * a + b * b, 1.0, places=12)
        with self.assertRaises(ValueError):
            NonUniformOLS([1.0, 0.0], 3, 6)  # non-power-of-two block
        with self.assertRaises(ValueError):
            NonUniformOLS([1.0, 0.0], 8, 12)  # tail not a multiple of block


if __name__ == "__main__":
    unittest.main()
