"""Fade curve math.

All curves are evaluated per sample at normalized positions ``t`` in
``[0.0, 1.0]`` where ``t = 0`` is the start of the overlap region.

Two curves are supported:

* ``LINEAR``: outgoing gain ``1 - t``, incoming gain ``t``.  For two signals
  that are identical and in phase the summed level stays perfectly constant
  (peak difference 0 dB); for uncorrelated signals the midpoint dips by
  3 dB RMS.
* ``EQUAL_POWER``: outgoing gain ``cos(t * pi/2)``, incoming gain
  ``sin(t * pi/2)``.  ``g_out**2 + g_in**2 == 1`` at every sample, so two
  uncorrelated signals of equal level keep constant RMS power; for identical
  in-phase signals the midpoint peaks at +3.01 dB.
"""

import math

LINEAR = "linear"
EQUAL_POWER = "equal_power"

_HALF_PI = math.pi / 2.0


def fade_curve(mode, t):
    """Return ``(gain_out, gain_in)`` at normalized position ``t``."""
    # Exact endpoint values keep seam samples deterministic and avoid the
    # 6e-17 residue of cos(pi/2).
    if t <= 0.0:
        return 1.0, 0.0
    if t >= 1.0:
        return 0.0, 1.0
    if mode == LINEAR:
        return 1.0 - t, t
    if mode == EQUAL_POWER:
        angle = t * _HALF_PI
        return math.cos(angle), math.sin(angle)
    raise ValueError(f"unknown fade mode: {mode!r}")


def fade_gains(mode, length):
    """Return per-sample ``(out_gain, in_gain)`` lists for ``length`` samples.

    Endpoints are pinned to the exact 0.0 / 1.0 values so seam samples never
    drift due to floating-point rounding.
    """
    if length <= 0:
        return [], []
    if length == 1:
        out_gain, in_gain = fade_curve(mode, 0.5)
        return [out_gain], [in_gain]
    out_gains = [0.0] * length
    in_gains = [0.0] * length
    last = length - 1
    for i in range(length):
        t = i / last
        out_gains[i], in_gains[i] = fade_curve(mode, t)
    out0, in0 = fade_curve(mode, 0.0)
    out1, in1 = fade_curve(mode, 1.0)
    out_gains[0], in_gains[0] = out0, in0
    out_gains[last], in_gains[last] = out1, in1
    return out_gains, in_gains
