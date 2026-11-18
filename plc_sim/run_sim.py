"""Opus packet-loss concealment simulation.

Pipeline: synthesize vowel/fricative concatenation -> Opus encode (16 kHz,
24 kbps CBR, 20 ms frames) -> drop packets (isolated + 3-packet bursts)
-> decode, concealing losses with each strategy -> metrics + WAV/spectrograms.

Concealment is strictly causal: strategies only see previously emitted
samples. The reference (loss-free decode) is never used to fill gaps.
"""
import os
import sys
import wave
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_audio import build, FS, SEGMENTS
from opus_codec import OpusEncoder, OpusDecoder
from plc import STRATEGIES, FRAME, _splice
import metrics

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "out")
os.makedirs(OUT, exist_ok=True)

# ---- loss patterns (frame indices) -------------------------------------
# segment layout: 5 segments x 0.35 s = 17.5 frames each (after xfade ~17)
# frame 0-1: leading silence; segments roughly: a:2-18 s:19-35 i:36-52
# sh:53-69 u:70-86
PATTERNS = {
    "iso_vowel_a":  [8],            # isolated loss inside /a/
    "iso_fric_s":   [26],           # isolated loss inside /s/
    "iso_vowel_i":  [44],           # isolated loss inside /i/
    "burst3_vowel_i": [45, 46, 47], # 3 consecutive losses inside /i/
    "burst3_vowel_u": [74, 75, 76], # 3 consecutive losses inside /u/
    "burst3_fric_sh": [60, 61, 62], # 3 consecutive losses inside /sh/
}


def save_wav(name, x):
    x = np.clip(x, -1.0, 1.0)
    pcm = (x * 32767).astype(np.int16)
    with wave.open(os.path.join(OUT, name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(pcm.tobytes())


def encode_all(sig):
    n_frames = len(sig) // FRAME
    enc = OpusEncoder(FS)
    pkts = []
    for f in range(n_frames):
        fr = (sig[f * FRAME:(f + 1) * FRAME] * 32767).astype(np.int16)
        pkts.append(enc.encode(fr))
    enc.close()
    return pkts, n_frames


# junction treatment per strategy: (entry crossfade samples, exit re-sync
# overlap samples).  silence/repeat are the naive hard-junction baselines;
# lpc_pred and opus_builtin use 5 ms raised-cosine smoothing (proper PLC).
# lpc_pred/opus_builtin need no entry fade: their fills are filter-state
# continuations of the history, hence already continuous at the seam.
JUNCTION = {
    "silence": (0, 0),
    "repeat": (0, 0),
    "lpc_pred": (0, 80),
    "opus_builtin": (0, 80),
}


def decode_stream(pkts, lost, strategy):
    """Decode packet stream; conceal `lost` frames with `strategy`.

    Decoder state is advanced with opus_decode(NULL) on loss (standard
    practice). "opus_builtin" keeps that PLC output directly; the other
    strategies replace it with their own causal fill.  Total output
    length always equals the loss-free decode length.
    """
    xfade_in, xfade_out = JUNCTION[strategy]
    dec = OpusDecoder(FS)
    out = np.zeros(0)
    fn = STRATEGIES.get(strategy)
    burst_idx = 0
    after_loss = False
    for i, pkt in enumerate(pkts):
        if i in lost:
            plc_pcm = dec.decode(None, FRAME).astype(np.float64) / 32768.0
            if strategy == "opus_builtin":
                fill = plc_pcm
            elif len(out):
                # generate xfade_in extra samples so the OLA joint keeps
                # the frame's net contribution at exactly FRAME samples
                fill = fn(out, FRAME + xfade_in, burst_idx)
            else:
                fill = np.zeros(FRAME)
            if xfade_in and len(out):
                out = _splice(out, fill, xfade_in)
            else:
                out = np.concatenate([out, fill])
            burst_idx += 1
            after_loss = True
        else:
            pcm = dec.decode(pkt, FRAME).astype(np.float64) / 32768.0
            if after_loss and xfade_out and len(out):
                # extend concealment past the gap, overlap-add with the
                # head of the good frame (NetEq-style merge); the total
                # length stays len(out) + FRAME.
                n = xfade_out
                if strategy == "opus_builtin":
                    extra = dec.decode(None, n).astype(np.float64) / 32768.0
                else:
                    extra = fn(out, n, burst_idx)
                w = 0.5 * (1 - np.cos(np.pi * np.arange(n) / n))
                blended = extra * (1 - w) + pcm[:n] * w
                out = np.concatenate([out, blended, pcm[n:]])
            else:
                out = np.concatenate([out, pcm])
            burst_idx = 0
            after_loss = False
    dec.close()
    return out


def evaluate(ref, test, lost, n_frames):
    lo, hi = metrics.segment_bounds(n_frames, lost)
    rmse, snr = metrics.region_rmse(ref, test, lo, hi)
    lsd = metrics.log_spectral_distance(ref, test, lo, hi)
    # junctions: start and end of the concealed region
    j = []
    for b in (lo, hi):
        if 0 < b < len(test):
            j.append(metrics.junction_metrics(ref, test, b))
    click_step = max(x[0] for x in j)
    click_hf = max(x[1] for x in j)
    hf_excess = max(x[2] for x in j)
    return dict(rmse=rmse, snr_db=snr, lsd_db=lsd, click_step=click_step,
                click_hf_dbfs=click_hf, hf_excess_db=hf_excess)


def main():
    sig = build()
    save_wav("source.wav", sig)
    pkts, n_frames = encode_all(sig)
    print(f"frames={n_frames}, pkt0={len(pkts[0])}B")

    ref = decode_stream(pkts, set(), "silence")  # no loss: strategy unused
    save_wav("reference_lossless_decode.wav", ref)

    rows = []
    for pat, lost in PATTERNS.items():
        lost_set = set(lost)
        for strat in list(STRATEGIES) + ["opus_builtin"]:
            test = decode_stream(pkts, lost_set, strat)
            assert len(test) == len(ref), (len(test), len(ref))
            m = evaluate(ref, test, lost, n_frames)
            rows.append((pat, strat, m))
            tag = f"{pat}__{strat}"
            save_wav(f"{tag}.wav", test)
            print(f"{tag}: rmse={m['rmse']:.4f} snr={m['snr_db']:.1f}dB "
                  f"lsd={m['lsd_db']:.2f}dB step={m['click_step']:.4f} "
                  f"hf={m['click_hf_dbfs']:.1f}dBFS "
                  f"xs={m['hf_excess_db']:.1f}dB")

    import csv
    with open(os.path.join(OUT, "metrics.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pattern", "strategy", "rmse", "snr_db", "lsd_db",
                    "click_step", "click_hf_dbfs", "hf_excess_db"])
        for pat, strat, m in rows:
            w.writerow([pat, strat, f"{m['rmse']:.5f}", f"{m['snr_db']:.2f}",
                        f"{m['lsd_db']:.3f}", f"{m['click_step']:.5f}",
                        f"{m['click_hf_dbfs']:.2f}",
                        f"{m['hf_excess_db']:.2f}"])
    np.save(os.path.join(OUT, "_ref.npy"), ref)
    print("done ->", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
