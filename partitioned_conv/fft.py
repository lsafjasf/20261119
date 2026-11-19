"""Radix-2 iterative FFT (Cooley-Tukey), pure Python standard library.

All transform lengths must be powers of two; the convolution module pads
filters/frames to the next power of two, so arbitrary signal lengths work.
Twiddle factors are cached per transform size to keep per-block cost low.
"""

from __future__ import annotations

import cmath
import math

_TWIDDLE_CACHE: dict[int, list[complex]] = {}


def is_power_of_two(n: int) -> bool:
    return n >= 1 and (n & (n - 1)) == 0


def next_power_of_two(n: int) -> int:
    if n <= 1:
        return 1
    return 1 << (n - 1).bit_length()


def _twiddles(n: int) -> list[complex]:
    """exp(-2j*pi*k/n) for k in 0..n/2-1, cached."""
    tbl = _TWIDDLE_CACHE.get(n)
    if tbl is None:
        tbl = [cmath.exp(-2j * math.pi * k / n) for k in range(n // 2)]
        _TWIDDLE_CACHE[n] = tbl
    return tbl


def fft(x: list[complex]) -> list[complex]:
    """In-place-style radix-2 DIT FFT; returns a new list."""
    n = len(x)
    if not is_power_of_two(n):
        raise ValueError("FFT length must be a power of two")
    if n == 1:
        return [complex(x[0])]

    # Bit-reversal permutation.
    a = [0j] * n
    j = 0
    for i in range(n):
        a[i] = complex(x[i])
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            a[i], a[j] = a[j], a[i]

    length = 2
    while length <= n:
        half = length >> 1
        tw = _twiddles(length)
        for start in range(0, n, length):
            for k in range(half):
                u = a[start + k]
                t = tw[k] * a[start + k + half]
                a[start + k] = u + t
                a[start + k + half] = u - t
        length <<= 1
    return a


def ifft(a: list[complex]) -> list[complex]:
    """Inverse FFT via conjugate-forward-conjugate (normalised by 1/N)."""
    n = len(a)
    conj = [v.conjugate() for v in a]
    y = fft(conj)
    return [v.conjugate() / n for v in y]


def fft_real(x: list[float]) -> list[complex]:
    return fft([complex(v) for v in x])


def real_circular_convolve(x: list[float], h: list[float]) -> list[float]:
    """Circular convolution of two real sequences of equal power-of-2 length."""
    if len(x) != len(h) or not is_power_of_two(len(x)):
        raise ValueError("both inputs must share a power-of-two length")
    spec_x = fft_real(x)
    spec_h = fft_real(h)
    prod = [a * b for a, b in zip(spec_x, spec_h)]
    y = ifft(prod)
    return [v.real for v in y]


def dft_naive(x: list[complex]) -> list[complex]:
    """O(N^2) reference DFT, used only by the test-suite cross check."""
    n = len(x)
    out = []
    for k in range(n):
        s = 0j
        for t in range(n):
            s += x[t] * cmath.exp(-2j * math.pi * k * t / n)
        out.append(s)
    return out
