"""Direct O(N*M) time-domain convolution, used as the ground-truth reference."""

from __future__ import annotations


class DirectConvolver:
    """Naive FIR convolver that also counts multiply-accumulate operations.

    One MAC = one real multiply + one add on a real sample pair.
    """

    def __init__(self, impulse: list[float]):
        self.impulse = list(impulse)
        self.mac_count = 0

    def process(self, signal: list[float]) -> list[float]:
        h = self.impulse
        n = len(signal)
        m = len(h)
        if m == 0 or n == 0:
            return []
        out_len = n + m - 1
        out = [0.0] * out_len
        for i, xi in enumerate(signal):
            if xi == 0.0:
                # Sparsity shortcut: no contribution, no MAC performed.
                continue
            for j, hj in enumerate(h):
                out[i + j] += xi * hj
                self.mac_count += 1
        return out

    def reset_counter(self) -> None:
        self.mac_count = 0


def direct_convolve(signal: list[float], impulse: list[float]) -> list[float]:
    return DirectConvolver(impulse).process(signal)
