"""Partitioned overlap-save (OLS) fast convolution.

Two flavours are provided:

* :class:`UniformOLS` - every filter partition has the same size ``B``;
* :class:`NonUniformOLS` - Gardner-style two-level layout where the short
  front section of the filter runs on small FFTs (hop ``B``) and the long
  tail section runs on large FFTs (hop ``C = k*B``).

Both convolvers are streaming: call :meth:`process` with arbitrary-length
input chunks (including partial final blocks), then :meth:`flush` to drain
the filter ring-down ("tail").  Output length always equals ``N + M - 1``.
"""

from __future__ import annotations

from .fft import fft_real, ifft, is_power_of_two, next_power_of_two


def _fft_butterfly_macs(n: int) -> int:
    """Complex MACs in one radix-2 FFT: (N/2) log2(N) butterflies."""
    if n <= 1:
        return 0
    return (n // 2) * (n.bit_length() - 1)


class UniformOLS:
    """Uniformly partitioned overlap-save convolver (one partition size)."""

    def __init__(self, impulse: list[float], block: int):
        if not is_power_of_two(block):
            raise ValueError("block size must be a power of two")
        self.block = block
        self.impulse = list(impulse)
        self.m = len(impulse)
        if self.m == 0:
            raise ValueError("impulse response must be non-empty")

        self.parts = (self.m + block - 1) // block
        self.fft_size = next_power_of_two(block + self.m - 1)
        n = self.fft_size

        # Filter spectra, one per partition (zero-padded chunks of B).
        self.filter_spectra: list[list[complex]] = []
        for p in range(self.parts):
            chunk = impulse[p * block:(p + 1) * block]
            frame = chunk + [0.0] * (n - len(chunk))
            self.filter_spectra.append(fft_real(frame))

        # Frequency-domain delay line of the last P input spectra.
        self.delay_line: list[list[complex] | None] = [None] * self.parts
        self._slot = 0
        # Real-sample history of length N (newest B appended each block).
        self.history = [0.0] * n

        self.complex_mac_count = 0
        self.blocks_processed = 0
        self._carry: list[float] = []
        self._samples_fed = 0

    # ---- accounting ----------------------------------------------------
    @property
    def latency_samples(self) -> int:
        """Causal block buffering delay: one hop of B samples."""
        return self.block

    def macs_per_block(self) -> int:
        """Complex MACs for one full input block (FFT + spectral + IFFT)."""
        n = self.fft_size
        return 2 * _fft_butterfly_macs(n) + self.parts * n

    def memory_slots(self) -> dict[str, int]:
        """Buffer usage expressed in real-float slots (1 complex = 2)."""
        n = self.fft_size
        filter_slots = 2 * self.parts * n
        delay_slots = 2 * self.parts * n
        return {
            "real_history": n,
            "filter_spectra_real_equiv": filter_slots,
            "input_delay_line_real_equiv": delay_slots,
            "total_real_equiv": n + filter_slots + delay_slots,
        }

    def reset_counter(self) -> None:
        self.complex_mac_count = 0

    # ---- streaming -----------------------------------------------------
    def _step(self, frame_b: list[float]) -> list[float]:
        """Process exactly one B-sample block (zero pad if shorter)."""
        n = self.fft_size
        b = self.block
        frame = list(frame_b) + [0.0] * (b - len(frame_b))
        self.history = self.history[b:] + frame
        spec = fft_real(self.history)
        self.delay_line[self._slot] = spec
        self._slot = (self._slot + 1) % self.parts

        acc = [0j] * n
        for p, hp in enumerate(self.filter_spectra):
            # Slot p holds the input spectrum from p blocks ago.
            src = self.delay_line[(self._slot - 1 - p) % self.parts]
            if src is None:
                continue
            for k in range(n):
                acc[k] += hp[k] * src[k]
        time_domain = ifft(acc)
        self.complex_mac_count += self.macs_per_block()
        self.blocks_processed += 1
        return [v.real for v in time_domain[n - b:]]

    def process(self, signal: list[float]) -> list[float]:
        """Feed any number of samples; partial trailing blocks are buffered."""
        self._samples_fed += len(signal)
        data = self._carry + list(signal)
        out: list[float] = []
        b = self.block
        full = len(data) // b
        for i in range(full):
            out.extend(self._step(data[i * b:(i + 1) * b]))
        self._carry = data[full * b:]
        return out

    def flush(self) -> list[float]:
        """Drain the partial block plus zero blocks for the full ring-down.

        The total number of B-output blocks of a length-N input and length-M
        filter is ceil((N + M - 1) / B); blocks already emitted are not
        re-emitted, so flush is safe after any number of process() calls.
        """
        if self._samples_fed == 0:
            return []
        # Blocks already emitted cover this many convolution samples; emit
        # only the blocks still needed to reach N + M - 1, then trim.
        total_samples = self._samples_fed + self.m - 1
        required_blocks = -(-total_samples // self.block)
        out: list[float] = []
        b = self.block
        if self._carry:
            out.extend(self._step(self._carry))
            self._carry = []
        while self.blocks_processed < required_blocks:
            out.extend(self._step([0.0] * b))
        emitted_samples = self.blocks_processed * b
        if emitted_samples > total_samples:
            out = out[: len(out) - (emitted_samples - total_samples)]
        return out

    def run(self, signal: list[float]) -> list[float]:
        """Convenience: full convolution trimmed to N + M - 1 samples."""
        out = self.process(signal)
        out.extend(self.flush())
        return out[: len(signal) + self.m - 1]


class NonUniformOLS:
    """Gardner-style two-level partitioned convolver.

    The first ``front_taps`` filter taps run on small FFTs with hop ``B``
    (short front section); the remaining taps are grouped into ``C = k*B``
    partitions and run on large FFTs (long tail section).  The small hop
    sets the algorithmic latency while the large FFTs keep MAC cost low for
    very long filters.
    """

    def __init__(
        self,
        impulse: list[float],
        block: int,
        tail_block: int,
        front_taps: int | None = None,
    ):
        if not (is_power_of_two(block) and is_power_of_two(tail_block)):
            raise ValueError("block sizes must be powers of two")
        if tail_block < block or tail_block % block != 0:
            raise ValueError("tail_block must be a multiple of block")
        m = len(impulse)
        if m == 0:
            raise ValueError("impulse response must be non-empty")

        self.block = block
        self.tail_block = tail_block
        if front_taps is None:
            front_taps = tail_block
        if front_taps % block != 0 or front_taps < block:
            raise ValueError("front_taps must be a positive multiple of block")
        front_taps = min(front_taps, m)
        self.front_taps = front_taps
        self.impulse = list(impulse)
        self.m = m

        self.front = UniformOLS(impulse[:front_taps], block)
        self.tail = (
            UniformOLS(impulse[front_taps:], tail_block)
            if front_taps < m
            else None
        )

        self._in_buf: list[float] = []
        self._tail_buf: list[float] = []
        self.out: list[float] = []
        self.front_pos = 0
        self.tail_pos = 0
        self._samples_fed = 0
        # Index up to which every assembled sample already contains BOTH its
        # front and tail contributions and is therefore a final, stable value.
        self._stable_pos = 0

    # ---- accounting ----------------------------------------------------
    @property
    def latency_samples(self) -> int:
        """Output starts after one short hop B; tail work never blocks it."""
        return self.block

    @property
    def complex_mac_count(self) -> int:
        total = self.front.complex_mac_count
        if self.tail is not None:
            total += self.tail.complex_mac_count
        return total

    def macs_per_full_cycle(self) -> dict[str, int]:
        """MACs spent across one C-sample cycle (k short blocks + 1 long)."""
        k = self.tail_block // self.block
        per = {
            "front_per_cycle": k * self.front.macs_per_block(),
            "tail_per_cycle": (
                self.tail.macs_per_block() if self.tail is not None else 0
            ),
        }
        per["total_per_cycle"] = (
            per["front_per_cycle"] + per["tail_per_cycle"]
        )
        return per

    def memory_slots(self) -> dict[str, int]:
        front_mem = self.front.memory_slots()["total_real_equiv"]
        tail_mem = (
            self.tail.memory_slots()["total_real_equiv"]
            if self.tail is not None
            else 0
        )
        return {
            "front_real_equiv": front_mem,
            "tail_real_equiv": tail_mem,
            "assembly_buffer": self.tail_block,
            "total_real_equiv": front_mem + tail_mem + self.tail_block,
        }

    def reset_counter(self) -> None:
        self.front.reset_counter()
        if self.tail is not None:
            self.tail.reset_counter()

    # ---- streaming -----------------------------------------------------
    def _add_at(self, offset: int, chunk: list[float]) -> None:
        need = offset + len(chunk)
        if need > len(self.out):
            self.out.extend([0.0] * (need - len(self.out)))
        for i, v in enumerate(chunk):
            self.out[offset + i] += v

    def _front_step(self, frame_b: list[float]) -> None:
        y = self.front._step(frame_b)
        self._add_at(self.front_pos, y)
        self.front_pos += len(y)

    def _tail_step(self, frame_c: list[float]) -> None:
        assert self.tail is not None
        y = self.tail._step(frame_c)
        # The tail sub-filter starts at tap `front_taps`, so its linear
        # convolution is shifted right by front_taps relative to global t=0.
        self._add_at(self.tail_pos + self.front_taps, y)
        self.tail_pos += len(y)

    def _stable_frontier(self) -> int:
        """Samples below this index have received all engine contributions."""
        if self.tail is None:
            return self.front_pos
        # Tail contribution at global index i exists once the tail engine has
        # emitted through i - front_taps (its own, unshifted output index).
        tail_cover = self.tail_pos + self.front_taps
        return min(self.front_pos, tail_cover)

    def _drain_stable(self, start: int) -> list[float]:
        frontier = self._stable_frontier()
        if frontier > self._stable_pos:
            self._stable_pos = frontier
        return self.out[start:frontier]

    def process(self, signal: list[float]) -> list[float]:
        if not signal:
            return []
        start = self._stable_pos
        self._samples_fed += len(signal)
        self._in_buf.extend(signal)
        b = self.block
        c = self.tail_block
        while len(self._in_buf) >= b:
            frame = self._in_buf[:b]
            del self._in_buf[:b]
            self._front_step(frame)
            if self.tail is not None:
                self._tail_buf.extend(frame)
            while self.tail is not None and len(self._tail_buf) >= c:
                tail_frame = self._tail_buf[:c]
                del self._tail_buf[:c]
                self._tail_step(tail_frame)
        return self._drain_stable(start)

    def flush(self) -> list[float]:
        start = self._stable_pos
        b = self.block
        c = self.tail_block
        if self._samples_fed == 0:
            return []
        n = self._samples_fed

        if self._in_buf:
            frame = self._in_buf
            self._in_buf = []
            self._front_step(frame)
            if self.tail is not None:
                self._tail_buf.extend(frame)

        # Each child flushes to its OWN convolution length.  The front
        # engine covers N + front_taps - 1; the tail covers N + tail_taps - 1
        # and is shifted right by front_taps during assembly.
        front_len = n + self.front_taps - 1
        front_need_blocks = -(-front_len // b)
        while self.front.blocks_processed < front_need_blocks:
            self._front_step([0.0] * b)

        # Tail engine: pad to a full C-frame, then zero blocks to cover tail.
        if self.tail is not None:
            if self._tail_buf:
                frame = self._tail_buf + [0.0] * (
                    c - len(self._tail_buf))
                self._tail_buf = []
                self._tail_step(frame)
            tail_len = n + (self.m - self.front_taps) - 1
            tail_need_blocks = -(-tail_len // c)
            while self.tail.blocks_processed < tail_need_blocks:
                self._tail_step([0.0] * c)
        # Everything is now assembled; mark the full convolution as stable.
        final_len = n + self.m - 1
        self._stable_pos = max(self._stable_pos, min(final_len, len(self.out)))
        return self.out[start:self._stable_pos]

    def run(self, signal: list[float]) -> list[float]:
        self.process(signal)
        self.flush()
        # Keep the full assembled length: child engines individually cover
        # only N + (their own taps) - 1, but their sum must span N + M - 1.
        return self.out[: len(signal) + self.m - 1]
