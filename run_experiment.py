"""NMF drum-separation experiment.

Pipeline
  1. Synthesise three known drums (kick / snare / hihat) and learn one
     spectral template block per drum from an isolated example hit.
  2. Generate two test mixtures from known event tables:
       A "overlap"      - many simultaneous / near-simultaneous hits
       B "timbre drift" - same voices rendered with slightly perturbed
                          synthesis parameters
  3. Separate each mixture with (a) locked templates and (b) templates
     retrained (semi-supervised) on the mixture.
  4. Report onset error, crosstalk energy and reconstruction residual.

The event tables are written to results/ground_truth/ and are consumed
only by the evaluation code - never by the separation itself.
"""

import json
import os

import numpy as np
from scipy.io import wavfile

from drumsep import synth, nmf, evaluate

SR = synth.SR
DRUMS = synth.DRUMS
OUT = "results"


def save_wav(path, x):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    y = np.clip(x, -1, 1)
    wavfile.write(path, SR, (y * 32767).astype(np.int16))


def make_overlap_events():
    rng = np.random.default_rng(42)
    events = []
    t = 0.2
    while t < 7.6:
        r = rng.random()
        if r < 0.30:  # simultaneous pair
            pair = rng.choice(DRUMS, size=2, replace=False)
            events += [(t, pair[0]), (t, pair[1])]
        elif r < 0.40:  # all three together
            events += [(t, d) for d in DRUMS]
        elif r < 0.60:  # near-overlap, 40 ms apart
            pair = rng.choice(DRUMS, size=2, replace=False)
            events += [(t, pair[0]), (t + 0.04, pair[1])]
        else:
            events.append((t, DRUMS[rng.integers(3)]))
        t += 0.25 + 0.25 * rng.random()
    return sorted(events)


def make_timbre_events():
    rng = np.random.default_rng(7)
    events = []
    t = 0.2
    while t < 7.6:
        events.append((t, DRUMS[rng.integers(3)]))
        t += 0.35 + 0.2 * rng.random()
    return events
    while t < 7.6:
        events.append((t, DRUMS[rng.integers(3)]))
        t += 0.35 + 0.2 * rng.random()
    return events


TIMBRE_DRIFT = {
    "kick": {"f0": 1.15, "f1": 1.10, "decay": 0.85},
    "snare": {"fbody": 1.12, "bp_lo": 0.9, "bp_hi": 0.92},
    "hihat": {"hp": 0.88, "decay": 1.3},
}


def separate_and_score(name, mix, stems, events, W_tpl, labels, tag):
    """Run locked + retrained separation on one mixture and score."""
    V, _, _ = nmf.mag_spectrogram(mix, SR)
    Z, _, _ = nmf.complex_spectrogram(mix, SR)
    stems_mag = {d: nmf.mag_spectrogram(stems[d], SR)[0] for d in DRUMS}
    rows = []
    for mode, lock in (("locked", True), ("retrained", False)):
        W, H = nmf.estimate_activations(V, W_tpl, lock_templates=lock)
        parts = nmf.wiener_separate(Z, W, H, labels, DRUMS)
        masks = {}
        Vh = W @ H + nmf.EPS
        for d in DRUMS:
            cols = [i for i, lab in enumerate(labels) if lab == d]
            masks[d] = (W[:, cols] @ H[cols, :]) / Vh
        sep = {d: nmf.invert_spectrogram(parts[d], SR, mix.size)
               for d in DRUMS}
        for d in DRUMS:
            save_wav(f"{OUT}/audio/{tag}_{mode}_{d}.wav", sep[d])
        det = evaluate.detect_onsets(H, labels, nmf.HOP, SR)
        ons = evaluate.onset_errors(det, events)
        xt = evaluate.crosstalk_db(masks, stems_mag, DRUMS)
        snr = evaluate.waveform_snr_db(sep, stems, DRUMS)
        res = evaluate.residual(V, W, H)
        rows.append({"mode": mode, "onset": ons, "crosstalk_db": xt,
                     "snr_db": snr, "residual": res})
    return rows


def fmt_report(all_results, counts):
    lines = ["# NMF 打击乐分离实验报告", ""]
    lines.append("合成三种鼓声（kick/snare/hihat），从孤立单击学习频谱模板，"
                 "再用 KL-NMF 分解混合录音。模板可锁定（只更新激活）或"
                 "在混合信号上重训（半监督）。事件表仅用于评估。")
    lines.append("")
    for exp, rows in all_results.items():
        lines.append(f"## 实验 {exp}")
        lines.append("")
        c = counts[exp]
        lines.append(
            f"事件总数 {sum(c.values())}（"
            + ", ".join(f"{d}: {c.get(d, 0)}" for d in DRUMS) + "）")
        lines.append("")
        for r in rows:
            lines.append(f"### 模式：{r['mode']}")
            lines.append("")
            lines.append("| 声部 | 起音误差均值(ms) | 最大(ms) | 匹配/漏检/误报 |"
                         " 串音能量(dB) | 波形SNR(dB) |")
            lines.append("|---|---|---|---|---|---|")
            for d in DRUMS:
                o = r["onset"][d]
                lines.append(
                    f"| {d} | {o['mean_ms']:.1f} | {o['max_ms']:.1f} | "
                    f"{o['matched']}/{o['missed']}/{o['false_alarms']} | "
                    f"{r['crosstalk_db'][d]:.1f} | {r['snr_db'][d]:.1f} |")
            lines.append(f"\n重构残差 ||V-WH||/||V|| = **{r['residual']:.4f}**")
            lines.append("")
    return "\n".join(lines)


def main():
    os.makedirs(f"{OUT}/audio", exist_ok=True)
    os.makedirs(f"{OUT}/ground_truth", exist_ok=True)

    hits = {d: synth.SYNTH[d]() for d in DRUMS}
    for d in DRUMS:
        save_wav(f"{OUT}/audio/template_{d}.wav", hits[d])
    W_tpl, labels, _ = nmf.learn_templates(hits)

    experiments = {
        "A_overlap": (make_overlap_events(), None),
        "B_timbre_drift": (make_timbre_events(), TIMBRE_DRIFT),
    }
    all_results = {}
    counts = {}
    for tag, (events, timbres) in experiments.items():
        mix, stems = synth.render_stems(events, 8.0, timbres)
        save_wav(f"{OUT}/audio/{tag}_mixture.wav", mix)
        for d in DRUMS:
            save_wav(f"{OUT}/audio/{tag}_true_{d}.wav", stems[d])
        with open(f"{OUT}/ground_truth/{tag}_events.json", "w") as fh:
            json.dump([[t, d] for t, d in events], fh, indent=1)
        all_results[tag] = separate_and_score(
            tag, mix, stems, events, W_tpl, labels, tag)
        counts[tag] = {}
        for _, d in events:
            counts[tag][d] = counts[tag].get(d, 0) + 1
        print(f"[done] {tag}")

    with open(f"{OUT}/report.md", "w") as fh:
        fh.write(fmt_report(all_results, counts))
    with open(f"{OUT}/metrics.json", "w") as fh:
        json.dump(all_results, fh, indent=1)
    print("report -> results/report.md")


if __name__ == "__main__":
    main()
