"""合成测试集：语音 / 键盘 / 空调噪声，逐段标注（标注仅用于评价）。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import numpy as np
from scipy.io import wavfile

SR = 16000


@dataclass
class Event:
    kind: str          # 'speech' | 'keyboard' | 'background'
    start: float
    end: float
    seed: int = 0


def _synth_syllable(rng: np.random.Generator, dur: float, amp: float) -> np.ndarray:
    """谐波叠加 + 共振峰包络 + 基频抖动，近似语音音节。"""
    n = int(dur * SR)
    t = np.arange(n) / SR
    f0 = rng.uniform(100, 220)
    f0_t = f0 * (1 + 0.03 * np.sin(2 * np.pi * rng.uniform(3, 6) * t))
    phase = 2 * np.pi * np.cumsum(f0_t) / SR
    sig = np.zeros(n)
    for h in range(1, 9):
        fh = f0 * h
        form = (np.exp(-((fh - 500) / 400) ** 2)
                + 0.6 * np.exp(-((fh - 1500) / 500) ** 2)
                + 0.3 * np.exp(-((fh - 2500) / 600) ** 2))
        sig += form * np.sin(h * phase + rng.uniform(0, 2 * np.pi))
    env = np.sin(np.pi * t / max(dur, 1e-6)) ** 0.7
    sig *= env
    sig /= max(1e-9, np.max(np.abs(sig)))
    return amp * sig


def _synth_speech(rng: np.random.Generator, dur: float, amp: float,
                  internal_pause: bool) -> np.ndarray:
    """音节序列；internal_pause 时在中部插入 0.25~0.35s 短停顿（测试合并）。"""
    parts: list[np.ndarray] = []
    remaining = dur
    pause_at = rng.uniform(0.35, 0.65) * dur if internal_pause and dur > 2.0 else None
    done = 0.0
    paused = False
    while remaining > 0.05:
        syl_dur = min(remaining, rng.uniform(0.15, 0.35))
        parts.append(_synth_syllable(rng, syl_dur, amp))
        remaining -= syl_dur
        done += syl_dur
        gap = rng.uniform(0.03, 0.08)
        if pause_at is not None and not paused and done >= pause_at:
            gap = rng.uniform(0.25, 0.35)
            paused = True
        gap = min(gap, remaining)
        parts.append(np.zeros(int(gap * SR)))
        remaining -= gap
    return np.concatenate(parts) if parts else np.zeros(1)


def _synth_click(rng: np.random.Generator, amp: float) -> np.ndarray:
    """单次键盘敲击：宽带噪声脉冲，快速指数衰减 + 高频共振。"""
    dur = rng.uniform(0.02, 0.05)
    n = int(dur * SR)
    t = np.arange(n) / SR
    burst = rng.standard_normal(n) * np.exp(-t / 0.004)
    res = np.sin(2 * np.pi * rng.uniform(2000, 5000) * t) * np.exp(-t / 0.006)
    sig = burst + 0.7 * res
    sig /= max(1e-9, np.max(np.abs(sig)))
    return amp * sig


def _synth_keyboard(rng: np.random.Generator, dur: float, amp: float) -> np.ndarray:
    """若干次敲击组成的键盘段。"""
    n = int(dur * SR)
    out = np.zeros(n)
    n_clicks = max(1, int(dur / rng.uniform(0.12, 0.3)))
    for _ in range(n_clicks):
        c = _synth_click(rng, amp * rng.uniform(0.7, 1.0))
        pos = rng.integers(0, max(1, n - len(c)))
        out[pos:pos + len(c)] += c
    return out


def _ac_bed(rng: np.random.Generator, n: int) -> np.ndarray:
    """空调本底：50Hz 嗡鸣及谐波 + 低通白噪声，全程存在。"""
    t = np.arange(n) / SR
    hum = 0.008 * np.sin(2 * np.pi * 50 * t) + 0.004 * np.sin(2 * np.pi * 150 * t)
    noise = rng.standard_normal(n)
    kernel = np.ones(64) / 64
    low = np.convolve(noise, kernel, mode="same")
    low /= max(1e-9, np.std(low))
    return hum + 0.006 * low


def default_events() -> list[Event]:
    """约 55s 时间线：语音(含段内短停顿)、键盘、空调本底/静音。"""
    return [
        Event("background", 0.0, 3.0),
        Event("speech", 3.0, 6.5, seed=11),
        Event("keyboard", 7.0, 7.9, seed=12),          # 连续敲击，不应触发开始
        Event("speech", 8.5, 12.5, seed=13),           # 含段内短停顿
        Event("keyboard", 13.0, 13.2, seed=14),        # 单击
        Event("speech", 13.6, 15.0, seed=15),          # 较短语音
        Event("background", 15.0, 18.0),
        Event("keyboard", 18.2, 20.0, seed=16),        # 长连击
        Event("speech", 20.8, 25.0, seed=17),
        Event("keyboard", 25.6, 26.1, seed=18),
        Event("speech", 26.8, 30.5, seed=19),          # 含段内短停顿
        Event("background", 30.5, 34.0),
        Event("keyboard", 34.3, 34.6, seed=20),
        Event("speech", 35.2, 38.0, seed=21),
        Event("background", 38.0, 45.0),               # 长静音(仅空调)
        Event("speech", 45.5, 49.0, seed=22),
        Event("keyboard", 49.5, 50.0, seed=23),
        Event("background", 50.0, 55.0),
    ]


def render(events: list[Event], master_seed: int = 7
           ) -> tuple[np.ndarray, list[dict]]:
    """渲染波形并生成逐段标注。语音段幅值有差异以模拟不同信噪比。"""
    total = max(e.end for e in events)
    n = int(total * SR)
    rng = np.random.default_rng(master_seed)
    mix = _ac_bed(rng, n)
    intervals: list[dict] = []
    speech_amps = [0.30, 0.22, 0.35, 0.26, 0.32, 0.24, 0.28]
    sp_idx = 0
    for ev in events:
        s, e = int(ev.start * SR), int(ev.end * SR)
        dur = ev.end - ev.start
        erng = np.random.default_rng(ev.seed)
        if ev.kind == "speech":
            internal = ev.seed in (13, 19)
            amp = speech_amps[sp_idx % len(speech_amps)]
            sp_idx += 1
            sig = _synth_speech(erng, dur, amp, internal)
            mix[s:s + len(sig)] += sig
            intervals.append({"start": ev.start, "end": ev.end, "label": "speech"})
        elif ev.kind == "keyboard":
            sig = _synth_keyboard(erng, dur, amp=0.5)
            mix[s:s + len(sig)] += sig
            intervals.append({"start": ev.start, "end": ev.end, "label": "keyboard"})
        else:
            intervals.append({"start": ev.start, "end": ev.end, "label": "background"})
    peak = np.max(np.abs(mix))
    if peak > 0.98:
        mix *= 0.98 / peak
    return mix.astype(np.float32), intervals


def generate(out_dir: str) -> tuple[str, str]:
    """生成测试集，返回 (wav路径, 标注路径)。"""
    os.makedirs(out_dir, exist_ok=True)
    events = default_events()
    mix, intervals = render(events)
    wav_path = os.path.join(out_dir, "testset.wav")
    ann_path = os.path.join(out_dir, "testset.ann.json")
    wavfile.write(wav_path, SR, (mix * 32767).astype(np.int16))
    ann = {
        "sample_rate": SR,
        "duration": max(e["end"] for e in intervals),
        "note": "标注仅用于评价，不参与检测。label: speech/keyboard/background(空调本底)",
        "intervals": intervals,
    }
    with open(ann_path, "w", encoding="utf-8") as f:
        json.dump(ann, f, ensure_ascii=False, indent=2)
    return wav_path, ann_path
