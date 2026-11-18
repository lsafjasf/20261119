"""合成一段"档案录音": 含停顿、频偏、强弱音不平衡、语音干扰,
并埋入应被拒识的过短音与重叠按键。输出波形与真值标注。
"""
from dataclasses import dataclass, field

import numpy as np

from .core import FS, DIGIT_TO_PAIR, ROW_FREQS, COL_FREQS


@dataclass
class TruthItem:
    digit: str
    start_sample: int
    end_sample: int
    kind: str               # valid | too_short | overlap
    note: str = ""


@dataclass
class SynthResult:
    signal: np.ndarray
    truth: list = field(default_factory=list)
    fs: int = FS


def _tone(digit, dur_s, fs, offset_pct=0.0, twist_db=0.0, amp=0.6, rng=None):
    """生成单个 DTMF 音, 支持频偏(%)与高低频电平差(twist, dB)。"""
    r, c = DIGIT_TO_PAIR[digit]
    f_lo = ROW_FREQS[r] * (1.0 + offset_pct / 100.0)
    f_hi = COL_FREQS[c] * (1.0 + offset_pct / 100.0)
    n = int(round(dur_s * fs))
    t = np.arange(n) / fs
    lo_g = 10.0 ** (-twist_db / 40.0)
    hi_g = 10.0 ** (twist_db / 40.0)
    ph1, ph2 = (rng.uniform(0, 2 * np.pi, 2) if rng is not None else (0.0, 0.0))
    sig = lo_g * np.sin(2 * np.pi * f_lo * t + ph1) + hi_g * np.sin(2 * np.pi * f_hi * t + ph2)
    # 5 ms 升降沿, 避免爆音
    ramp = min(n // 2, int(0.005 * fs))
    if ramp > 0:
        env = np.ones(n)
        env[:ramp] = np.linspace(0, 1, ramp)
        env[-ramp:] = np.linspace(1, 0, ramp)
        sig *= env
    return amp * sig / 2.0


def _speech(dur_s, fs, rng, amp=0.25):
    """类语音干扰: 谐波簇 + 共振峰包络 + 音节级调幅 + 气息噪声。"""
    n = int(round(dur_s * fs))
    t = np.arange(n) / fs
    f0 = rng.uniform(100, 180)
    sig = np.zeros(n)
    for h in range(1, 12):
        f = f0 * h
        if f > fs / 2 - 100:
            break
        # 共振峰包络: 模拟 500/1500/2500 Hz 附近的峰
        formant = (np.exp(-((f - 500) ** 2) / (2 * 250 ** 2))
                   + 0.6 * np.exp(-((f - 1500) ** 2) / (2 * 400 ** 2))
                   + 0.3 * np.exp(-((f - 2500) ** 2) / (2 * 500 ** 2)))
        sig += formant * np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi))
    syll = 0.5 + 0.5 * np.sin(2 * np.pi * rng.uniform(3, 5) * t) ** 2
    breath = 0.15 * rng.standard_normal(n)
    sig = sig * syll + breath
    sig /= max(np.abs(sig).max(), 1e-9)
    return amp * sig


def synthesize(seed=7, fs=FS) -> SynthResult:
    rng = np.random.default_rng(seed)
    parts = []          # 波形段
    truth: list[TruthItem] = []
    cursor = 0          # 当前样本位置

    def push(sig):
        nonlocal cursor
        parts.append(sig)
        start = cursor
        cursor += len(sig)
        return start, cursor

    def silence(dur_s):
        push(np.zeros(int(round(dur_s * fs))))

    def add_digit(digit, dur_s, kind="valid", note="", **kw):
        s, e = push(_tone(digit, dur_s, fs, rng=rng, **kw))
        truth.append(TruthItem(digit, s, e, kind, note))

    silence(0.4)
    # --- 有效按键: 随机频偏(±1.5% 内)与 twist(±6 dB 内) ---
    valid_digits = list("1602580374") + ["*", "#"]
    offsets = [0.8, -1.2, 0.0, 1.5, -0.6, 0.4, -1.5, 1.0, -0.3, 0.9, -1.0, 0.5]
    twists = [3.0, -4.0, 0.0, 6.0, -6.0, 2.0, -2.5, 4.5, -5.0, 1.0, -3.5, 5.5]
    durs = [0.12, 0.09, 0.15, 0.08, 0.11, 0.10, 0.14, 0.09, 0.12, 0.10, 0.13, 0.08]
    for i, d in enumerate(valid_digits):
        add_digit(d, durs[i], "valid",
                  note=f"offset={offsets[i]}%, twist={twists[i]}dB",
                  offset_pct=offsets[i], twist_db=twists[i],
                  amp=float(rng.uniform(0.45, 0.7)))
        silence(float(rng.uniform(0.15, 0.35)))

    # --- 过短音(20 ms), 必须拒识 ---
    add_digit("5", 0.020, "too_short", "20ms 过短音")
    silence(0.3)
    add_digit("9", 0.025, "too_short", "25ms 过短音")
    silence(0.3)

    # --- 重叠按键: 两个音无静音间隔直接相连, 必须拒识 ---
    s1, e1 = push(_tone("3", 0.12, fs, rng=rng))
    s2, e2 = push(_tone("7", 0.12, fs, rng=rng))
    truth.append(TruthItem("3", s1, e1, "overlap", "与 7 无间隔"))
    truth.append(TruthItem("7", s2, e2, "overlap", "与 3 无间隔"))
    silence(0.35)

    # --- 纯语音干扰段: 不应产生任何按键 ---
    speech1 = _speech(1.2, fs, rng, amp=0.35)
    push(speech1)
    silence(0.25)

    # --- 语音干扰背景下的有效按键 ---
    for d, off, tw in [("4", 0.7, -3.0), ("8", -0.9, 3.5)]:
        tone = _tone(d, 0.12, fs, offset_pct=off, twist_db=tw, amp=0.6, rng=rng)
        bg = _speech(len(tone) / fs, fs, rng, amp=0.18)
        s, e = push(tone + bg)
        truth.append(TruthItem(d, s, e, "valid", "语音背景共存"))
        silence(0.3)

    # --- 结尾语音 ---
    push(_speech(0.8, fs, rng, amp=0.3))
    silence(0.3)

    signal = np.concatenate(parts)
    # 全程加性噪声 (SNR 约 25 dB)
    noise = rng.standard_normal(len(signal))
    signal = signal + 0.03 * np.std(signal) / np.std(noise) * noise
    signal = (0.95 * signal / np.abs(signal).max()).astype(np.float64)
    return SynthResult(signal=signal, truth=truth, fs=fs)
