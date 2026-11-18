"""Blind audio watermarking via QIM on sub-band energy ratio.

Scheme (publicly described "band-energy quantization" family):
- Two contiguous sub-bands A (1.5-3 kHz) and B (3.5-5 kHz) are used, with a
  guard interval so window mainlobe leakage does not mix the bands.
- Each frame carries one bit, embedded by quantizing
      d = 10*log10(E_A / E_B)
  onto one of two interleaved lattices spaced 2*DELTA apart, offset by DELTA.
- Detection is blind: no original audio needed. Gain changes cancel in the
  ratio; a sync preamble + sliding correlation recovers alignment after
  truncation; a CRC-8 checksum (nonzero init, so the all-zero codeword is
  invalid) prevents unwatermarked audio from being accepted as a message.
"""

import numpy as np

FS = 44100
FRAME = 4096                 # samples per symbol frame (~93 ms)
F_A_LO, F_A_HI = 1500.0, 3000.0  # sub-band A (Hz)
F_B_LO, F_B_HI = 3500.0, 5000.0  # sub-band B (Hz)
SYNC_LEN = 16
PAYLOAD_LEN = 16             # message bits
CRC_LEN = 8
MIN_MARGIN_DB = 0.10         # acceptance threshold on mean |margin|

# Fixed sync preamble (seeded PN, good aperiodic autocorrelation).
_rng = np.random.default_rng(20261119)
SYNC_BITS = _rng.integers(0, 2, SYNC_LEN).tolist()
SYNC_BIPOLAR = np.array([2 * b - 1 for b in SYNC_BITS], dtype=float)

_WIN = np.hanning(FRAME)
_BAND_A = np.arange(int(np.ceil(F_A_LO * FRAME / FS)), int(F_A_HI * FRAME / FS) + 1)
_BAND_B = np.arange(int(np.ceil(F_B_LO * FRAME / FS)), int(F_B_HI * FRAME / FS) + 1)


def crc8(bits, poly=0x07, init=0xFF):
    """CRC-8 over a list of bits (MSB first), nonzero init."""
    reg = init
    for bit in bits:
        fb = ((reg >> 7) & 1) ^ int(bit)
        reg = ((reg << 1) & 0xFF) ^ (poly if fb else 0)
    return reg


def int_to_bits(value, nbits):
    return [(value >> (nbits - 1 - i)) & 1 for i in range(nbits)]


def bits_to_int(bits):
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return v


def build_codeword(payload_bits):
    """payload bits + CRC-8 -> protected bit list."""
    crc = crc8(payload_bits)
    return list(payload_bits) + int_to_bits(crc, CRC_LEN)


def total_frames():
    return SYNC_LEN + PAYLOAD_LEN + CRC_LEN


def _band_db_ratio(frame):
    """Log energy ratio d = 10*log10(E_A/E_B) of one windowed frame."""
    spec = np.fft.rfft(frame * _WIN)
    mag2 = np.abs(spec) ** 2
    ea = mag2[_BAND_A].sum()
    eb = mag2[_BAND_B].sum()
    return 10.0 * np.log10(max(ea, 1e-18) / max(eb, 1e-18))


def _quantize(d, bit, delta):
    """Nearest point of lattice for `bit`: {2*delta*k + bit*delta}."""
    return np.round((d - bit * delta) / (2.0 * delta)) * (2.0 * delta) + bit * delta


def embed_frame(frame, bit, delta):
    """Embed one bit into a frame; returns watermarked frame (click-free)."""
    spec = np.fft.rfft(frame * _WIN)
    mag2 = np.abs(spec) ** 2
    ea = mag2[_BAND_A].sum()
    eb = mag2[_BAND_B].sum()
    d = 10.0 * np.log10(max(ea, 1e-18) / max(eb, 1e-18))
    shift_db = _quantize(d, bit, delta) - d
    # Split the shift symmetrically: A gets +shift/2, B gets -shift/2 (energy),
    # so overall frame energy is roughly preserved.
    gain = np.ones_like(spec, dtype=float)
    gain[_BAND_A] = 10.0 ** (shift_db / 40.0)
    gain[_BAND_B] = 10.0 ** (-shift_db / 40.0)
    # The difference of two windowed frames already tapers to zero at the
    # frame edges, so adding it directly introduces no boundary clicks.
    wm_diff = np.fft.irfft(spec * gain, FRAME) - frame * _WIN
    return frame + wm_diff


def embed(signal, payload_bits, delta):
    """Embed sync + payload + CRC into a float signal. Returns new signal."""
    bits = SYNC_BITS + build_codeword(payload_bits)
    out = np.array(signal, dtype=float).copy()
    n = len(out)
    for i, bit in enumerate(bits):
        s = i * FRAME
        if s + FRAME > n:
            raise ValueError("signal too short for payload")
        out[s:s + FRAME] = embed_frame(out[s:s + FRAME], bit, delta)
    return out


def _frame_decision(frame, delta):
    """Return (bit, margin_db, signed_conf) for one frame.

    margin_db: distance of d to the nearest decision boundary (>= 0).
    signed_conf: +margin for bit 1, -margin for bit 0.
    """
    d = _band_db_ratio(frame)
    r0 = abs(d - _quantize(d, 0, delta))
    r1 = abs(d - _quantize(d, 1, delta))
    bit = 0 if r0 <= r1 else 1
    margin = delta / 2.0 - min(r0, r1)
    return bit, margin, margin if bit else -margin


MIN_SYNC_OVERLAP = 6      # partial preamble (truncated head) still matches
DRIFT_GRID = np.arange(-0.0125, 0.0126, 0.0025)  # rel. frame-rate hypotheses


def detect(signal, delta, offset_steps=16):
    """Blind detection with sync search.

    Handles truncation of the head (partial-preamble correlation, normalized
    by sqrt of the overlap length) and mild resampling (per-codeword drift
    hypothesis search maximizing the sum of decision margins).

    Returns dict with keys:
      found, crc_ok, accepted, sync_frame, sync_offset_samples, sync_time_s,
      sync_score, sync_overlap, drift, payload_bits, payload_int,
      mean_margin_db, bit_details.
    """
    x = np.asarray(signal, dtype=float)
    step = FRAME // offset_steps
    nbits = PAYLOAD_LEN + CRC_LEN
    best = None
    for off in range(0, FRAME, step):
        nfr = (len(x) - off) // FRAME
        if nfr < SYNC_LEN + nbits - (SYNC_LEN - MIN_SYNC_OVERLAP):
            continue
        conf = np.empty(nfr)
        for i in range(nfr):
            fr = x[off + i * FRAME: off + (i + 1) * FRAME]
            conf[i] = _frame_decision(fr, delta)[2]
        # sliding correlation; negative s = preamble partly cut off at head
        s_lo = -(SYNC_LEN - MIN_SYNC_OVERLAP)
        s_hi = nfr - (SYNC_LEN + nbits)
        for s in range(s_lo, s_hi + 1):
            j0 = max(0, -s)
            idx = np.arange(s + j0, s + SYNC_LEN)
            cnt = len(idx)
            score = float(np.dot(conf[idx], SYNC_BIPOLAR[j0:]) / np.sqrt(cnt))
            if best is None or score > best[0]:
                best = (score, off, s, cnt)
    if best is None:
        return {"found": False, "crc_ok": False, "accepted": False}
    score, off, s, overlap = best
    p0 = off + s * FRAME  # sample position of first sync frame (may be < 0)

    # Drift search: pick the frame-rate hypothesis maximizing the total
    # decision margin over the codeword frames, then decode with it.
    best_dec = None
    for rate in DRIFT_GRID:
        details = []
        ok = True
        for k in range(nbits):
            p = int(round(p0 + (SYNC_LEN + k) * FRAME * (1.0 + rate)))
            if p < 0 or p + FRAME > len(x):
                ok = False
                break
            b, m, c = _frame_decision(x[p:p + FRAME], delta)
            details.append((b, m, c))
        if not ok:
            continue
        tot = sum(d[1] for d in details)
        if best_dec is None or tot > best_dec[0]:
            best_dec = (tot, rate, details)
    if best_dec is None:
        return {"found": False, "crc_ok": False, "accepted": False}
    _, rate, dec_details = best_dec

    dec = [d[0] for d in dec_details]
    payload = dec[:PAYLOAD_LEN]
    crc_bits = dec[PAYLOAD_LEN:]
    crc_ok = crc8(payload) == bits_to_int(crc_bits)
    details = [{
        "index": j,
        "field": "payload" if j < PAYLOAD_LEN else "crc",
        "bit": int(dec_details[j][0]),
        "margin_db": float(dec_details[j][1]),
        "signed_conf_db": float(dec_details[j][2]),
    } for j in range(nbits)]
    mean_margin = float(np.mean([d["margin_db"] for d in details]))
    return {
        "found": True,
        "crc_ok": bool(crc_ok),
        "accepted": bool(crc_ok and mean_margin >= MIN_MARGIN_DB),
        "sync_frame": int(s),
        "sync_offset_samples": int(off),
        "sync_time_s": p0 / FS,
        "sync_score": score,
        "sync_overlap": int(overlap),
        "drift": float(rate),
        "payload_bits": payload,
        "payload_int": bits_to_int(payload),
        "mean_margin_db": mean_margin,
        "bit_details": details,
    }
