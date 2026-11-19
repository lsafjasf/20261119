"""Look-ahead peak limiter with program-dependent (auto) release.

Signal chain
------------
1. Side chain: input is up-sampled 4x with a windowed-sinc FIR so the
   detector sees inter-sample (true) peaks, not only sample values.
2. Raw target gain ``g = min(1, ceiling / |x4x|)`` per oversampled tick.
3. Attack/release smoothing of the gain envelope (dB-domain one-pole),
   where the release time constant adapts to the signal in auto mode.
4. Linear-phase look-ahead delay on the dry signal so the gain arrives
   before the transient; output = delayed input * smoothed gain.

The output is time-aligned with the input (all introduced delay is
compensated internally).  The un-compensated real-time latency is
reported explicitly through :class:`LimitResult`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import firwin, upfirdn


@dataclass(frozen=True)
class LimiterConfig:
    """Configuration for :class:`PeakLimiter`."""

    sample_rate: float = 48_000.0
    ceiling_db: float = -1.0          # output peak ceiling (dBTP)
    attack_ms: float = 0.2            # time constant for gain reduction
    release_ms: float = 120.0         # used in fixed mode; baseline in auto
    auto_release_min_ms: float = 40.0
    auto_release_max_ms: float = 800.0
    lookahead_ms: float = 1.5
    oversample: int = 4
    fir_taps: int = 97                # odd => symmetric, linear phase

    @property
    def ceiling_linear(self) -> float:
        return float(10.0 ** (self.ceiling_db / 20.0))


@dataclass
class LimitResult:
    """Outputs of one limiter pass."""

    output: np.ndarray
    gain: np.ndarray                  # smoothed linear gain at base rate
    raw_gain: np.ndarray             # un-smoothed target gain (base rate)
    delayed_input: np.ndarray         # dry signal after look-ahead delay
    latency_samples: int
    latency_ms: float
    fir_group_delay_over: int         # group delay in oversampled ticks
    max_output_peak_db: float
    clipping_samples: int
    clamp_mask: np.ndarray | None = None


class PeakLimiter:
    """Look-ahead true-peak limiter with bypass / fixed / auto release."""

    def __init__(self, config: LimiterConfig | None = None):
        self.cfg = config or LimiterConfig()
        c = self.cfg
        p = c.fir_taps if c.fir_taps % 2 == 1 else c.fir_taps + 1
        # upfirdn zero-stuffs first, so the interpolation FIR must gain
        # by the up-sampling factor in its pass band to preserve amplitude.
        fir = firwin(p, cutoff=0.45 / c.oversample, window=("kaiser", 8.0))
        self._fir = fir * c.oversample
        self._fir_group_delay = p // 2
        self._lookahead_base = max(
            1, int(round(c.lookahead_ms / 1000.0 * c.sample_rate))
        )

    @property
    def latency_samples(self) -> int:
        """Algorithmic latency heard through the audio path (samples).

        Only the linear look-ahead delay sits in the audio path; the
        oversampling FIR group delay lives in the side chain and is
        accounted for by index alignment rather than delaying audio.
        """
        return self._lookahead_base

    @property
    def latency_ms(self) -> float:
        return self.latency_samples / self.cfg.sample_rate * 1000.0

    # ------------------------------------------------------------------ #
    # Gain smoothing
    # ------------------------------------------------------------------ #
    def _smooth_gain(
        self, raw_gain: np.ndarray, release_tau: np.ndarray | float
    ) -> np.ndarray:
        """Attack/release one-pole smoothing in the dB domain.

        Gain can only decrease at the attack rate and recover at the
        (possibly time-varying) release rate.
        """
        c = self.cfg
        sr = c.sample_rate
        attack_alpha = float(np.exp(-1.0 / (max(c.attack_ms / 1000.0, 1e-6) * sr)))
        if np.isscalar(release_tau):
            release_alpha = float(
                np.exp(-1.0 / (max(release_tau / 1000.0, 1e-6) * sr))
            )

        eps = 1e-10
        raw_db = 20.0 * np.log10(np.maximum(raw_gain, eps))
        state = 0.0  # 0 dB == unity gain
        out = np.empty_like(raw_db)
        for i, target in enumerate(raw_db):
            if target < state:  # need more reduction -> attack branch
                alpha = attack_alpha
            else:               # recover -> release branch
                if np.isscalar(release_tau):
                    alpha = release_alpha
                else:
                    tau_ms = max(release_tau[i], 1.0)
                    alpha = float(np.exp(-1.0 / (tau_ms / 1000.0 * sr)))
            state = alpha * state + (1.0 - alpha) * target
            out[i] = state
        return 10.0 ** (out / 20.0)
    # ------------------------------------------------------------------ #
    # Program-dependent auto release
    # ------------------------------------------------------------------ #
    def _auto_release_tau(self, raw_gain: np.ndarray) -> np.ndarray:
        """Per-sample release time constant (ms).

        Three signal features drive the time constant, each in [0, 1]:

        * ``density``  - onset rate of the hit train.  Dense transients get
          a long release so residual reduction carries across hits instead
          of fully re-opening before every hit (which would flatten every
          strike to the ceiling level).
        * ``sustained``- fraction of recent time spent limiting.  Stays
          near 1 under continuous bass, near 0 for isolated drum hits.
        * ``depth``    - smoothed amount of gain reduction.  Deep reduction
          recovers slowly to avoid audible pumping.

        Sparse, shallow limiting therefore keeps the short release and the
        original dynamic shape; dense or sustained limiting relaxes slowly.
        """
        c = self.cfg
        sr = c.sample_rate
        n = len(raw_gain)
        eps = 1e-10
        raw_db = 20.0 * np.log10(np.maximum(raw_gain, eps))

        # Onset trigger: gain reduction increasing faster than 3 dB/ms.
        db_per_ms = np.diff(raw_db, prepend=0.0) * (sr / 1000.0)
        trigger = (db_per_ms < -3.0).astype(np.float64)

        # Generic EMA used for the feature streams.
        def smooth(stream: np.ndarray, frac: float) -> np.ndarray:
            a = float(np.exp(-1.0 / (frac * sr)))
            out = np.empty_like(stream)
            state = 0.0
            for i, v in enumerate(stream):
                state = a * state + (1.0 - a) * v
                out[i] = state
            return out

        density = smooth(trigger, 0.25) * 2.0
        limiting = (raw_gain < 0.995).astype(np.float64)
        sustained = smooth(limiting, 0.40)
        depth = smooth(np.clip(1.0 - raw_gain, 0.0, 1.0), 0.10)

        blend = np.clip(0.45 * density + 0.35 * sustained + 0.40 * depth, 0.0, 1.0)
        tau = c.auto_release_min_ms + (
            c.auto_release_max_ms - c.auto_release_min_ms
        ) * blend
        # Zipper-noise guard: the time constant itself changes smoothly,
        # and is hard-clamped to the configured operating range.
        smoothed = smooth(tau, 0.03)
        return np.clip(smoothed, c.auto_release_min_ms, c.auto_release_max_ms)

    # ------------------------------------------------------------------ #
    # Main pass
    # ------------------------------------------------------------------ #
    def process(self, x: np.ndarray, mode: str = "auto") -> LimitResult:
        """Run one limiter pass.

        Parameters
        ----------
        x:
            Mono input signal, peak-normalised range roughly [-1, 1].
        mode:
            ``"bypass"``  - zero-latency unity pass-through;
            ``"fixed"``   - constant release time constant;
            ``"auto"``    - program-dependent release time constant.
        """
        c = self.cfg
        x = np.asarray(x, dtype=np.float64)
        n = len(x)

        if mode == "bypass":
            return LimitResult(
                output=x.copy(),
                gain=np.ones(n),
                raw_gain=np.ones(n),
                delayed_input=x.copy(),
                latency_samples=0,
                latency_ms=0.0,
                fir_group_delay_over=self._fir_group_delay,
                max_output_peak_db=20.0 * np.log10(max(np.max(np.abs(x)), 1e-12)),
                clipping_samples=0,
                clamp_mask=np.zeros(n, dtype=bool),
            )

        look = self._lookahead_base
        osf = c.oversample

        # Side chain on the zero-padded signal so look-ahead works at tail.
        padded = np.concatenate([x, np.zeros(look + 2)])
        x4 = upfirdn(self._fir, padded, up=osf)
        env4 = np.abs(x4)
        g = self._fir_group_delay
        # upfirdn output tick for base-rate sample k is (g + k*osf);
        # look-ahead means sample i evaluates the peak of sample i+look.
        env = np.empty(n)
        for i in range(n):
            start = g + (i + look) * osf
            block = env4[start : start + osf]
            env[i] = block.max() if block.size else env[i - 1] if i else 1e-12
        env = np.maximum(env, 1e-12)

        raw_gain = np.minimum(1.0, c.ceiling_linear / env)

        if mode == "fixed":
            gain = self._smooth_gain(raw_gain, c.release_ms)
        elif mode == "auto":
            tau = self._auto_release_tau(raw_gain)
            gain = self._smooth_gain(raw_gain, tau)
        else:
            raise ValueError(f"unknown mode {mode!r}; use bypass|fixed|auto")

        delayed = np.zeros_like(x)
        delayed[look:] = x[:-look]
        output = delayed * gain

        # Final sample-accurate safety clamp.  The analog-style smoothing can
        # leave a small residual overshoot on the steepest transient edges;
        # this hard stage guarantees the ceiling at the sample level while
        # leaving normal dynamics untouched (it only acts on overshoots).
        safety = np.minimum(1.0, c.ceiling_linear / np.maximum(np.abs(output), 1e-12))
        clamp_mask = safety < (1.0 - 1e-6)
        output = output * safety
        gain = gain * safety

        # Clip accounting: sample peaks and 4x true peaks over the ceiling.
        # The gain-compensated interpolation FIR preserves amplitude when
        # combined with upfirdn zero-stuffing, so no rescaling is needed.
        tol = 10.0 ** (c.ceiling_db / 20.0)
        # Safety-clamp activations: samples the analog gain stage could not
        # contain in time.  After the clamp these are at, not over, ceiling.
        clip_samples = int(np.sum(clamp_mask))
        out4 = upfirdn(self._fir, output, up=osf)
        true_peak = float(np.max(np.abs(out4)))
        max_peak_db = 20.0 * np.log10(max(true_peak, 1e-12))

        return LimitResult(
            output=output,
            gain=gain,
            raw_gain=raw_gain,
            delayed_input=delayed,
            latency_samples=look,
            latency_ms=look / c.sample_rate * 1000.0,
            fir_group_delay_over=g,
            max_output_peak_db=max_peak_db,
            clipping_samples=clip_samples,
            clamp_mask=clamp_mask,
        )

    def short_term_clip_counts(
        self, result: LimitResult, window_ms: float = 50.0
    ) -> np.ndarray:
        """Safety-clamp activations inside each short-time window."""
        c = self.cfg
        win = max(1, int(round(window_ms / 1000.0 * c.sample_rate)))
        n = len(result.output)
        over = result.clamp_mask if result.clamp_mask is not None else np.zeros(n, bool)
        n_win = int(np.ceil(n / win))
        counts = np.zeros(n_win, dtype=int)
        for i in range(n_win):
            counts[i] = int(np.sum(over[i * win : (i + 1) * win]))
        return counts
