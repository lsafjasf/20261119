"""Continuous-phase FSK (CPFSK) text modem over mono WAV files.

Protocol (all fields MSB-first):
  - Preamble : 32 alternating bits (0xAA x4), used for detection + timing
  - Sync word: 16 bits, 0xEB90, marks end of preamble / start of header
  - Length   : 8 bits, payload length in bytes (1..255)
  - Payload  : length bytes
  - CRC-16   : CCITT (poly 0x1021, init 0xFFFF) over (length byte + payload)

Modulation:
  - 8000 Hz sample rate, 16-bit PCM mono
  - Symbol duration 8 ms (64 samples/symbol, 125 baud)
  - bit 0 -> 1200 Hz, bit 1 -> 2200 Hz, phase continuous across symbols

The receiver searches for the preamble at an unknown start offset, jointly
estimates symbol timing and slight sample-clock stretch, then verifies the
sync word and CRC. Frames that fail any check are rejected; the decoder
never fabricates payload it did not actually recover.
"""

import math
import struct
import wave

SAMPLE_RATE = 8000
SYMBOL_SAMPLES = 64                 # 8 ms symbols -> 125 baud
FREQ_0 = 1200.0                     # bit 0
FREQ_1 = 2200.0                     # bit 1
AMPLITUDE = 0.8

PREAMBLE_BITS = [1, 0] * 16         # 32 alternating bits
SYNC_WORD = 0xEB90
SYNC_BITS = [(SYNC_WORD >> (15 - i)) & 1 for i in range(16)]
MAX_PAYLOAD = 255

HEADER_TRAILER_BITS = len(PREAMBLE_BITS) + len(SYNC_BITS) + 8 + 16


# ---------------------------------------------------------------- CRC-16

def _make_crc_table():
    table = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table.append(crc)
    return table


_CRC_TABLE = _make_crc_table()


def crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC_TABLE[((crc >> 8) ^ byte) & 0xFF]
    return crc


# ------------------------------------------------------------ bit packing

def bytes_to_bits(data):
    bits = []
    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    return bits


def bits_to_bytes(bits):
    if len(bits) % 8:
        raise ValueError("bit count not a multiple of 8")
    out = bytearray()
    for i in range(0, len(bits), 8):
        byte = 0
        for bit in bits[i:i + 8]:
            byte = (byte << 1) | bit
        out.append(byte)
    return bytes(out)


def frame_bits(payload):
    """Build the full frame bit sequence for a payload (bytes)."""
    if not 1 <= len(payload) <= MAX_PAYLOAD:
        raise ValueError("payload must be 1..%d bytes" % MAX_PAYLOAD)
    body = bytes([len(payload)]) + bytes(payload)
    crc = crc16(body)
    bits = list(PREAMBLE_BITS) + list(SYNC_BITS)
    bits += bytes_to_bits(body)
    bits += [(crc >> (15 - i)) & 1 for i in range(16)]
    return bits


# --------------------------------------------------------------- modulator

def modulate(bits, sample_rate=SAMPLE_RATE, symbol_samples=SYMBOL_SAMPLES,
             freq0=FREQ_0, freq1=FREQ_1, amplitude=AMPLITUDE):
    """CPFSK-modulate a bit sequence into float samples in [-1, 1]."""
    step0 = 2.0 * math.pi * freq0 / sample_rate
    step1 = 2.0 * math.pi * freq1 / sample_rate
    samples = []
    phase = 0.0
    for bit in bits:
        step = step1 if bit else step0
        for _ in range(symbol_samples):
            phase += step
            samples.append(amplitude * math.cos(phase))
    return samples


# ----------------------------------------------------------------- WAV I/O

def write_wav(path, samples, sample_rate=SAMPLE_RATE):
    pcm = bytearray()
    for s in samples:
        s = max(-1.0, min(1.0, s))
        pcm += struct.pack("<h", int(round(s * 32767)))
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(pcm))


def read_wav(path):
    with wave.open(path, "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError("expecting 16-bit mono WAV")
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    count = len(raw) // 2
    ints = struct.unpack("<%dh" % count, raw)
    return rate, [v / 32768.0 for v in ints]


# ------------------------------------------------------------- demodulator

class DecodeError(Exception):
    """Raised when a frame cannot be recovered; payload is never guessed."""


def _mix_cumulative(samples, freq, sample_rate):
    """Cumulative sums of samples mixed down by exp(-j 2 pi freq t).

    Symbol energy over [i, i+n) is then |C[i+n] - C[i]|^2 in O(1).
    """
    step = 2.0 * math.pi * freq / sample_rate
    c_re = [0.0] * (len(samples) + 1)
    c_im = [0.0] * (len(samples) + 1)
    re = im = 0.0
    cos = math.cos
    sin = math.sin
    for i, s in enumerate(samples):
        re += s * cos(step * i)
        im -= s * sin(step * i)
        c_re[i + 1] = re
        c_im[i + 1] = im
    return c_re, c_im


def _energy_arrays(samples, sample_rate, symbol_samples):
    """Per-position symbol energy at both mark/space frequencies."""
    n = len(samples) - symbol_samples
    if n <= 0:
        raise DecodeError("signal shorter than one symbol")
    e = []
    for freq in (FREQ_0, FREQ_1):
        c_re, c_im = _mix_cumulative(samples, freq, sample_rate)
        arr = [0.0] * n
        for i in range(n):
            dr = c_re[i + symbol_samples] - c_re[i]
            di = c_im[i + symbol_samples] - c_im[i]
            arr[i] = dr * dr + di * di
        e.append(arr)
    return e[0], e[1]  # energy at FREQ_0, energy at FREQ_1


_SEARCH_PATTERN = list(PREAMBLE_BITS) + list(SYNC_BITS)


def _pattern_score(d1, offsets_per_bit, first=0, last=None, step=1):
    """Score every start offset against the known preamble+sync pattern.

    d1[i] = e1[i] - e0[i]; offsets_per_bit[k] = integer position of bit k
    relative to the frame start. Returns (best_offset, best_score).
    """
    limit = len(d1) - offsets_per_bit[-1]
    if limit <= 0:
        raise DecodeError("signal too short for a frame header")
    if last is None or last > limit:
        last = limit
    offs = list(range(first, last, step))
    acc = [0.0] * len(offs)
    for k, bit in enumerate(_SEARCH_PATTERN):
        pos = offsets_per_bit[k]
        if bit:
            for j in range(len(offs)):
                acc[j] += d1[offs[j] + pos]
        else:
            for j in range(len(offs)):
                acc[j] -= d1[offs[j] + pos]
    best_j = 0
    best = acc[0]
    for j in range(1, len(offs)):
        if acc[j] > best:
            best = acc[j]
            best_j = j
    return offs[best_j], best


def _find_frame_start(d1, symbol_samples, max_stretch):
    """Jointly estimate frame start offset and sample-clock stretch."""
    sym = symbol_samples
    nbits = len(_SEARCH_PATTERN)

    def offsets_for(stretch):
        return [int(round(k * sym * (1.0 + stretch))) for k in range(nbits)]

    # Coarse pass: full offset range with step 2 over a stretch grid.
    step = max_stretch / 4.0
    stretches = [-max_stretch + i * step for i in range(9)]
    candidates = []
    for stretch in stretches:
        off, score = _pattern_score(d1, offsets_for(stretch), step=2)
        candidates.append((score, stretch, off))
    candidates.sort(reverse=True)
    _, rough_stretch, rough_off = candidates[0]

    # Refinement passes: local offset window, finer stretch grid.
    best = None
    off, stretch = rough_off, rough_stretch
    for _ in range(2):
        step /= 4.0
        stretches = [stretch + (i - 4) * step for i in range(9)]
        candidates = []
        for st in stretches:
            o, sc = _pattern_score(d1, offsets_for(st),
                                   first=max(0, off - 4), last=off + 5)
            candidates.append((sc, st, o))
        candidates.sort(reverse=True)
        score, stretch, off = candidates[0]
        best = (off, stretch, score)
    return best  # (offset, stretch, score)


def demodulate(samples, sample_rate=SAMPLE_RATE, symbol_samples=SYMBOL_SAMPLES,
               max_stretch=0.01):
    """Decode one frame from raw samples.

    Returns (payload_bytes, info_dict). Raises DecodeError on any failure;
    the payload is never substituted or guessed.
    """
    e0, e1 = _energy_arrays(samples, sample_rate, symbol_samples)
    d1 = [a - b for a, b in zip(e1, e0)]
    offset, stretch, score = _find_frame_start(d1, symbol_samples, max_stretch)

    sym = symbol_samples
    n = len(d1)

    def bit_at(pos):
        i = int(round(pos))
        if i < 0 or i >= n:
            raise DecodeError("frame runs past end of signal")
        return 1 if d1[i] > 0 else 0

    # Verify the located preamble + sync against the known pattern.
    mismatches = 0
    for k, want in enumerate(_SEARCH_PATTERN):
        if bit_at(offset + k * sym * (1.0 + stretch)) != want:
            mismatches += 1
    if mismatches > len(_SEARCH_PATTERN) // 6:
        raise DecodeError("preamble/sync mismatch (%d/%d bits wrong)"
                          % (mismatches, len(_SEARCH_PATTERN)))

    # Read the length byte straight from the pattern-timed positions;
    # drift this early in the frame is negligible.
    hdr_pos = offset + len(_SEARCH_PATTERN) * sym * (1.0 + stretch)
    length = 0
    for k in range(8):
        length = (length << 1) | bit_at(hdr_pos + k * sym * (1.0 + stretch))
    if length == 0:
        raise DecodeError("declared payload length is zero")

    # Refine the stretch estimate against the whole frame: the energy
    # margin |e1 - e0| at correct symbol centers does not depend on the
    # bit values, so we can maximize it before decoding anything.
    total_bits = len(_SEARCH_PATTERN) + 8 + length * 8 + 16

    def frame_margin(st):
        total = 0.0
        for k in range(total_bits):
            i = int(round(offset + k * sym * (1.0 + st)))
            if 0 <= i < n:
                total += abs(d1[i])
        return total

    best_st, best_m = stretch, -1.0
    for i in range(-10, 11):
        st = stretch + i * 1e-4
        m = frame_margin(st)
        if m > best_m:
            best_m, best_st = m, st
    stretch = best_st

    # Walk the remaining symbols with an early-late timing tracker.
    pos = offset + len(_SEARCH_PATTERN) * sym * (1.0 + stretch)
    adjust = 0.0

    def next_bit():
        nonlocal pos, adjust
        center = int(round(pos + adjust))
        best_d, best_margin = 0, None
        for d in (-2, -1, 0, 1, 2):
            i = center + d
            if 0 <= i < n:
                margin = abs(d1[i])
                if best_margin is None or margin > best_margin:
                    best_margin, best_d = margin, d
        adjust = max(-8.0, min(8.0, adjust + 0.25 * best_d))
        i = center + best_d
        if i < 0 or i >= n:
            raise DecodeError("frame runs past end of signal")
        bit = 1 if d1[i] > 0 else 0
        pos += sym * (1.0 + stretch)
        return bit

    def read_bits(count):
        return [next_bit() for _ in range(count)]

    length_bits = read_bits(8)
    length_check = 0
    for b in length_bits:
        length_check = (length_check << 1) | b
    if length_check != length:
        raise DecodeError("length byte changed after timing refinement")

    payload = bits_to_bytes(read_bits(length * 8))
    crc_bits = read_bits(16)
    got_crc = 0
    for b in crc_bits:
        got_crc = (got_crc << 1) | b

    want_crc = crc16(bytes([length]) + payload)
    if got_crc != want_crc:
        raise DecodeError("CRC mismatch (got 0x%04X, want 0x%04X)"
                          % (got_crc, want_crc))

    info = {
        "offset": offset,
        "stretch": stretch,
        "score": score,
        "pattern_mismatches": mismatches,
        "length": length,
    }
    return payload, info


# ------------------------------------------------------------------ channel

def channel(samples, noise=0.0, stretch=0.0, seed=None):
    """Simulate a channel: sample-clock stretch + additive Gaussian noise.

    stretch: fractional duration change (e.g. 0.003 = 0.3% longer).
    noise:   standard deviation of AWGN relative to full-scale amplitude.
    """
    import random
    rng = random.Random(seed)

    if stretch:
        n_out = int(round(len(samples) * (1.0 + stretch)))
        out = []
        for i in range(n_out):
            pos = i / (1.0 + stretch)
            j = int(pos)
            frac = pos - j
            if j + 1 < len(samples):
                out.append(samples[j] * (1.0 - frac) + samples[j + 1] * frac)
            else:
                out.append(samples[min(j, len(samples) - 1)])
        samples = out

    if noise > 0.0:
        gauss = rng.gauss
        samples = [s + gauss(0.0, noise) for s in samples]
    return samples


# ---------------------------------------------------------------------- CLI

def _cmd_tx(args):
    payload = args.text.encode("utf-8")
    bits = frame_bits(payload)
    samples = modulate(bits)
    if args.noise or args.stretch:
        samples = channel(samples, noise=args.noise, stretch=args.stretch,
                          seed=args.seed)
    # Silence padding so the receiver must locate the frame.
    samples = [0.0] * args.pad + samples + [0.0] * args.pad
    write_wav(args.output, samples)
    print("wrote %s: %d payload bytes, %d bits, %d samples (%.2f s)"
          % (args.output, len(payload), len(bits), len(samples),
             len(samples) / SAMPLE_RATE))


def _cmd_rx(args):
    rate, samples = read_wav(args.input)
    if rate != SAMPLE_RATE:
        print("warning: sample rate %d, expected %d" % (rate, SAMPLE_RATE))
    try:
        payload, info = demodulate(samples, sample_rate=rate)
    except DecodeError as exc:
        print("decode FAILED: %s" % exc)
        return 1
    print("decoded %d bytes: %r" % (len(payload), payload))
    try:
        print("text: %s" % payload.decode("utf-8"))
    except UnicodeDecodeError:
        print("text: (not valid UTF-8)")
    print("info: offset=%(offset)d stretch=%(stretch).5f "
          "pattern_mismatches=%(pattern_mismatches)d" % info)
    return 0


def _cmd_demo(args):
    payload = args.text.encode("utf-8")
    tx_bits = frame_bits(payload)
    n_payload_bits = len(payload) * 8

    ok = crc_fail = sync_fail = 0
    bit_errors = 0
    for trial in range(args.trials):
        samples = modulate(tx_bits)
        samples = channel(samples, noise=args.noise, stretch=args.stretch,
                          seed=args.seed + trial)
        samples = [0.0] * 200 + samples + [0.0] * 200
        try:
            decoded, _ = demodulate(samples)
        except DecodeError as exc:
            if "CRC" in str(exc):
                crc_fail += 1
            else:
                sync_fail += 1
            continue
        ok += 1
        rx_bits = bytes_to_bits(decoded)
        tx_payload_bits = bytes_to_bits(payload)
        for a, b in zip(rx_bits, tx_payload_bits):
            if a != b:
                bit_errors += 1

    decoded_frames = ok
    total_bits = decoded_frames * n_payload_bits
    ber = (bit_errors / total_bits) if total_bits else float("nan")
    print("trials:            %d" % args.trials)
    print("noise sigma:       %.4f" % args.noise)
    print("stretch:           %+.4f%%" % (args.stretch * 100.0))
    print("frames OK:         %d" % ok)
    print("CRC failures:      %d" % crc_fail)
    print("sync/search fails: %d" % sync_fail)
    print("payload bit errors:%d / %d" % (bit_errors, total_bits))
    print("BER (CRC-passing frames): %.6f" % ber)
    return 0


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="CPFSK text modem over WAV")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tx", help="encode text to WAV")
    p.add_argument("--text", required=True)
    p.add_argument("-o", "--output", default="tx.wav")
    p.add_argument("--noise", type=float, default=0.0)
    p.add_argument("--stretch", type=float, default=0.0)
    p.add_argument("--pad", type=int, default=400,
                   help="silence samples added at both ends")
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(func=_cmd_tx)

    p = sub.add_parser("rx", help="decode WAV back to text")
    p.add_argument("-i", "--input", required=True)
    p.set_defaults(func=_cmd_rx)

    p = sub.add_parser("demo", help="loopback test through simulated channel")
    p.add_argument("--text", default="Hello, CPFSK!")
    p.add_argument("--noise", type=float, default=0.1)
    p.add_argument("--stretch", type=float, default=0.003)
    p.add_argument("--trials", type=int, default=10)
    p.add_argument("--seed", type=int, default=1)
    p.set_defaults(func=_cmd_demo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
