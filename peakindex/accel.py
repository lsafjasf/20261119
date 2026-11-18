"""Grouped min/max reduction with an optional C accelerator."""
import ctypes
import os
import subprocess
import sys

_LIB = None
_TRIED = False


def _load_lib():
    global _LIB, _TRIED
    if _TRIED:
        return _LIB
    _TRIED = True
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, "_reduce.c")
    so = os.path.join(here, "_reduce.so")
    try:
        if not os.path.exists(so) or os.path.getmtime(so) < os.path.getmtime(src):
            subprocess.check_call(
                ["cc", "-O2", "-shared", "-fPIC", src, "-o", so])
        lib = ctypes.CDLL(so)
        lib.minmax_reduce.restype = ctypes.c_size_t
        lib.minmax_reduce.argtypes = [
            ctypes.POINTER(ctypes.c_int16), ctypes.c_size_t,
            ctypes.c_size_t, ctypes.POINTER(ctypes.c_int16),
            ctypes.POINTER(ctypes.c_int16),
        ]
        _LIB = lib
    except Exception as exc:  # no compiler -> pure python fallback
        print("peakindex: C accelerator unavailable (%s); "
              "using pure Python fallback" % exc, file=sys.stderr)
        _LIB = None
    return _LIB


def _as_i16(buf):
    return ctypes.cast(buf, ctypes.POINTER(ctypes.c_int16))


def minmax_reduce(flat, group):
    """Reduce array('h') `flat` into ceil(len/group) (min, max) pairs.

    `flat` may be raw samples (group=factor) or interleaved (min,max)
    pairs of a lower level (group=2*factor). Returns (mins, maxs) as
    array('h') of length ceil(len/group).
    """
    from array import array
    n = len(flat)
    n_groups = (n + group - 1) // group
    out_min = array("h", bytes(2 * n_groups))
    out_max = array("h", bytes(2 * n_groups))
    if n == 0:
        return out_min, out_max
    lib = _load_lib()
    if lib is not None:
        src = _as_i16(flat.buffer_info()[0])
        got = lib.minmax_reduce(src, n, group,
                                _as_i16(out_min.buffer_info()[0]),
                                _as_i16(out_max.buffer_info()[0]))
        if got != n_groups:
            raise RuntimeError("minmax_reduce count mismatch")
        return out_min, out_max
    for i in range(n_groups):
        seg = flat[i * group:(i + 1) * group]
        out_min[i] = min(seg)
        out_max[i] = max(seg)
    return out_min, out_max
