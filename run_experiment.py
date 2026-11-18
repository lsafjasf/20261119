"""Blind audio watermark extraction experiment.

Pipeline:
  1. Synthesize a deterministic music-like 6 s source clip (44.1 kHz mono).
  2. Embed a 16-bit payload (+CRC-8, +sync preamble) with the sub-band
     energy QIM scheme from watermark.py at several strengths DELTA.
  3. Blind-detect under four conditions: clean, gain change, mild
     resampling (+0.5%), and truncation of the first 0.45 s.
  4. Negative controls: unwatermarked signals must be rejected.
  5. Write CSV artifacts, WAV artifacts, and a trade-off figure.
"""

import csv
import os

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly

import watermark as wm

ROOT = os.path.dirname(os.path.abspath(__file__))
WAV_DIR = os.path.join(ROOT, "artifacts", "wav")
CSV_DIR = os.path.join(ROOT, "artifacts", "csv")
FIG_DIR = os.path.join(ROOT, "artifacts", "figures")

PAYLOAD = 0x574D  # "WM"
PAYLOAD_BITS = wm.int_to_bits(PAYLOAD, wm.PAYLOAD_LEN)
DELTAS = [0.5, 1.0, 2.0, 3.0, 4.0, 6.0]
DETAIL_DELTA = 3.0
DUR_S = 6.0


def synth_source(seed=7, dur=DUR_S, fs=wm.FS):
    """Deterministic music-like clip: chord pads + melody + bass + noise."""
    rng = np.random.default_rng(seed)
    n = int(dur * fs)
    t = np.arange(n) / fs
    sig = np.zeros(n)

    def note(freq, t0, t1, amp, harmonics=5, vibr=0.0):
        i0, i1 = int(t0 * fs), min(int(t1 * fs), n)
        tt = np.arange(i1 - i0) / fs
        env = np.minimum(1.0, tt / 0.02) * np.exp(-tt / max(t1 - t0, 0.1) * 1.5)
        phase = 2 * np.pi * freq * tt
        if vibr:
            phase += vibr * np.sin(2 * np.pi * 5.0 * tt)
        tone = sum((0.55 ** h) * np.sin((h + 1) * phase)
                   for h in range(harmonics))
        sig[i0:i1] += amp * env * tone

    chords = [[261.6, 329.6, 392.0], [196.0, 246.9, 293.7],
              [220.0, 261.6, 329.6], [174.6, 220.0, 261.6]]
    for ci, chord in enumerate(chords * 2):
        for f in chord:
            note(f, ci * 0.75, ci * 0.75 + 0.8, 0.10, harmonics=4)
    scale = [523.3, 587.3, 659.3, 784.0, 880.0, 1046.5]
    for k in range(16):
        note(scale[rng.integers(0, len(scale))], 0.35 * k + 0.1,
             0.35 * k + 0.42, 0.16, harmonics=3, vibr=0.4)
    for k in range(8):
        note(65.4 * (1 + (k % 3)), 0.75 * k, 0.75 * k + 0.5, 0.20, harmonics=2)
    sig += 0.004 * rng.standard_normal(n)
    sig /= max(1.0, np.max(np.abs(sig)) / 0.7)
    return sig.astype(np.float64)


def save_wav(path, x):
    pcm = np.round(np.clip(x, -1, 1) * 32767).astype(np.int16)
    wavfile.write(path, wm.FS, pcm)


def snr_db(ref, test):
    n = min(len(ref), len(test))
    err = ref[:n] - test[:n]
    return 10 * np.log10(np.sum(ref[:n] ** 2) / max(np.sum(err ** 2), 1e-30))


def attack_gain(x):
    return x * 0.6  # about -4.4 dB


def attack_resample(x):
    return resample_poly(x, 200, 201)  # +0.5% playback rate


def attack_truncate(x):
    return x[int(0.45 * wm.FS):]  # drop first 0.45 s (non-integer frames)


ATTACKS = [
    ("clean", lambda x: x),
    ("gain_-4.4dB", attack_gain),
    ("resample_+0.5pct", attack_resample),
    ("truncate_0.45s", attack_truncate),
]


def evaluate(marked, delta, expected_bits):
    """Run detection under every attack; return list of result dicts."""
    rows = []
    for name, fn in ATTACKS:
        attacked = fn(marked)
        res = wm.detect(attacked, delta)
        row = {"attack": name, "delta": delta}
        if res["found"]:
            dec = [d["bit"] for d in res["bit_details"]]
            ber = float(np.mean([a != b for a, b in zip(dec, expected_bits)]))
            row.update(res, ber=ber,
                       payload_ok=res["payload_bits"] == PAYLOAD_BITS)
            row["bit_details"] = res["bit_details"]
        else:
            row.update(ber=1.0, crc_ok=False, accepted=False,
                       payload_ok=False, bit_details=[])
        rows.append(row)
    return rows


def negative_controls(delta):
    """Unwatermarked material must not be accepted as a valid message."""
    cases = {
        "clean_source": synth_source(seed=7),
        "other_music": synth_source(seed=123),
        "white_noise": 0.3 * np.random.default_rng(5).standard_normal(
            int(DUR_S * wm.FS)),
        "silence": np.zeros(int(DUR_S * wm.FS)),
    }
    rows = []
    for name, sig in cases.items():
        res = wm.detect(sig, delta)
        rows.append({
            "signal": name,
            "found": res.get("found", False),
            "crc_ok": res.get("crc_ok", False),
            "accepted": res.get("accepted", False),
            "payload_int": res.get("payload_int", -1),
            "mean_margin_db": res.get("mean_margin_db", 0.0),
            "sync_score": res.get("sync_score", 0.0),
        })
    return rows


def main():
    for d in (WAV_DIR, CSV_DIR, FIG_DIR):
        os.makedirs(d, exist_ok=True)

    source = synth_source()
    save_wav(os.path.join(WAV_DIR, "source_clean.wav"), source)
    expected = wm.build_codeword(PAYLOAD_BITS)

    sweep_rows, bit_rows = [], []
    for delta in DELTAS:
        marked = wm.embed(source, PAYLOAD_BITS, delta)
        tag = ("%.1f" % delta).replace(".", "p")
        save_wav(os.path.join(WAV_DIR, "marked_d%s.wav" % tag), marked)
        distortion = snr_db(source, marked)
        for row in evaluate(marked, delta, expected):
            bit_details = row.pop("bit_details")
            row["snr_db"] = distortion
            sweep_rows.append(row)
            if delta == DETAIL_DELTA:
                for d in bit_details:
                    bit_rows.append({
                        "attack": row["attack"], "bit_index": d["index"],
                        "field": d["field"], "expected_bit": expected[d["index"]],
                        "decoded_bit": d["bit"], "margin_db": d["margin_db"],
                        "correct": d["bit"] == expected[d["index"]],
                    })
        print("delta=%.1f dB  SNR=%.1f dB  " % (delta, distortion) + "  ".join(
            "%s:BER=%.2f,%s" % (r["attack"], r["ber"],
                                "OK" if r["accepted"] else "rej")
            for r in sweep_rows[-len(ATTACKS):]))

    # Attacked WAVs at the detail strength, for listening/inspection.
    marked3 = wm.embed(source, PAYLOAD_BITS, DETAIL_DELTA)
    for name, fn in ATTACKS[1:]:
        save_wav(os.path.join(WAV_DIR, "marked_d3p0_%s.wav" % name),
                 fn(marked3))

    neg_rows = negative_controls(DETAIL_DELTA)

    with open(os.path.join(CSV_DIR, "sweep.csv"), "w", newline="") as f:
        keys = ["delta", "attack", "snr_db", "ber", "payload_ok", "crc_ok",
                "accepted", "mean_margin_db", "sync_frame",
                "sync_offset_samples", "sync_time_s", "sync_score",
                "payload_int"]
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(sweep_rows)
    with open(os.path.join(CSV_DIR, "per_bit_delta3.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(bit_rows[0].keys()))
        w.writeheader()
        w.writerows(bit_rows)
    with open(os.path.join(CSV_DIR, "negative_controls.csv"), "w",
              newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(neg_rows[0].keys()))
        w.writeheader()
        w.writerows(neg_rows)

    make_figure(sweep_rows)
    print_report(sweep_rows, bit_rows, neg_rows)


def print_report(sweep_rows, bit_rows, neg_rows):
    print("\n===== Strength sweep (payload 0x%04X, %d payload + %d CRC bits) ====="
          % (PAYLOAD, wm.PAYLOAD_LEN, wm.CRC_LEN))
    hdr = ("DELTA", "attack", "SNR_dB", "BER", "crc", "accepted",
           "mean_margin_dB", "sync_frame", "sync_off_smp", "sync_t_s")
    print("%-5s %-18s %-7s %-5s %-4s %-8s %-14s %-10s %-12s %-8s" % hdr)
    for r in sweep_rows:
        print("%-5.1f %-18s %-7.1f %-5.2f %-4s %-8s %-14.3f %-10s %-12s %-8.3f"
              % (r["delta"], r["attack"], r["snr_db"], r["ber"],
                 r.get("crc_ok"), r.get("accepted"), r.get("mean_margin_db", 0),
                 r.get("sync_frame", "-"), r.get("sync_offset_samples", "-"),
                 r.get("sync_time_s", 0.0)))

    print("\n===== Per-bit decision distances (DELTA=%.1f dB) =====" % DETAIL_DELTA)
    for name, _ in ATTACKS:
        rows = [b for b in bit_rows if b["attack"] == name]
        line = " ".join(
            "%d:%d/%+.2f%s" % (b["bit_index"], b["decoded_bit"],
                               b["margin_db"] * (1 if b["decoded_bit"] else -1),
                               "" if b["correct"] else "!")
            for b in rows)
        n_err = sum(1 for b in rows if not b["correct"])
        print("[%s] errors=%d\n  idx:bit/signed_margin_db  %s"
              % (name, n_err, line))

    print("\n===== Negative controls (unwatermarked, DELTA=%.1f dB) ====="
          % DETAIL_DELTA)
    for r in neg_rows:
        print("%-14s crc_ok=%-5s accepted=%-5s best_payload=0x%04X "
              "mean_margin=%.3f dB sync_score=%.2f"
              % (r["signal"], r["crc_ok"], r["accepted"],
                 max(r["payload_int"], 0), r["mean_margin_db"],
                 r["sync_score"]))
    verdict = all(not r["accepted"] for r in neg_rows)
    print("negative-control verdict:", "PASS (all rejected)" if verdict
          else "FAIL (false acceptance!)")


def make_figure(sweep_rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    snr = {r["delta"]: r["snr_db"] for r in sweep_rows if r["attack"] == "clean"}
    ax1.plot(list(snr), list(snr.values()), "o-", color="black")
    ax1.set_xlabel("quantization step DELTA (dB)")
    ax1.set_ylabel("watermark SNR (dB, higher = less distortion)")
    ax1.set_title("Strength vs. audible distortion")
    ax1.grid(alpha=0.3)
    for name, _ in ATTACKS:
        pts = [(r["delta"], 1.0 - r["ber"]) for r in sweep_rows
               if r["attack"] == name]
        pts.sort()
        ax2.plot([p[0] for p in pts], [p[1] for p in pts], "o-", label=name)
    ax2.set_xlabel("quantization step DELTA (dB)")
    ax2.set_ylabel("extraction success (1 - BER)")
    ax2.set_ylim(-0.05, 1.05)
    ax2.set_title("Strength vs. extraction robustness")
    ax2.grid(alpha=0.3)
    ax2.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG_DIR, "tradeoff.png"), dpi=140)


if __name__ == "__main__":
    main()
