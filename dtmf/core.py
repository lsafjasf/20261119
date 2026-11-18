"""DTMF 核心:已知频率 Goertzel 分帧检测 + 持续时间状态机。

检测不是简单取全频谱最强两个峰,而是在 8 个 ITU 标准频率上
做 Goertzel 能量估计,再在低频组/高频组内分别选峰并做
组内占优度、扭转(twist)与噪声门限校验。
"""
from dataclasses import dataclass, field

import numpy as np

FS = 8000
ROW_FREQS = [697.0, 770.0, 852.0, 941.0]
COL_FREQS = [1209.0, 1336.0, 1477.0, 1633.0]
ALL_FREQS = ROW_FREQS + COL_FREQS

KEYPAD = {
    (0, 0): "1", (0, 1): "2", (0, 2): "3", (0, 3): "A",
    (1, 0): "4", (1, 1): "5", (1, 2): "6", (1, 3): "B",
    (2, 0): "7", (2, 1): "8", (2, 2): "9", (2, 3): "C",
    (3, 0): "*", (3, 1): "0", (3, 2): "#", (3, 3): "D",
}
DIGIT_TO_PAIR = {v: k for k, v in KEYPAD.items()}

FRAME_LEN = 256          # 32 ms @ 8 kHz
HOP = 80                 # 10 ms
MIN_TONE_MS = 40.0       # 最短有效按键
MIN_SILENCE_MS = 30.0    # 判定按键结束所需的静音间隔
MAX_DROPOUT_FRAMES = 2   # 按键内部允许的瞬时掉线帧数
DOMINANCE_RATIO = 2.0    # 组内最强/次强功率比门限 (约 3 dB)
TWIST_DB_LIMIT = 8.0     # 允许的强弱音不平衡范围
SNR_GATE = 8.0           # 双音功率相对噪声底的最小倍数
CONCENTRATION_MIN = 6.0  # 双音能量/全带能量比, 抑制宽带语音
FOREIGN_PATIENCE = 2     # 连续异数字帧达到该数才判数字跳变


@dataclass
class FrameLabel:
    index: int
    time_ms: float
    digit: str | None
    row_freq: float | None
    col_freq: float | None
    energies: np.ndarray
    valid: bool
    fail_checks: list = field(default_factory=list)


@dataclass
class Event:
    digit: str
    start_sample: int
    end_sample: int
    status: str            # "accepted" | "rejected"
    reason: str
    confirmed: bool
    duration_ms: float = 0.0
    transitions: list = field(default_factory=list)


def goertzel_energies(frame: np.ndarray, freqs=ALL_FREQS, fs=FS) -> np.ndarray:
    """加 Hann 窗后对每个已知频率做单频相关能量估计。"""
    n = len(frame)
    window = np.hanning(n)
    xw = frame * window
    t = np.arange(n) / fs
    out = np.empty(len(freqs))
    for i, f in enumerate(freqs):
        c = np.exp(-2j * np.pi * f * t)
        out[i] = abs(np.dot(xw, c)) ** 2
    return out


def label_frame(energies: np.ndarray, noise_floor: float, total_power: float):
    """返回 (digit, row_idx, col_idx, fail_checks)。"""
    row_e = energies[:4]
    col_e = energies[4:]
    r = int(np.argmax(row_e))
    c = int(np.argmax(col_e))
    fails = []

    def dominance(group, best):
        others = np.delete(group, best)
        second = others.max() if others.size else 1e-12
        return group[best] / max(second, 1e-12)

    if dominance(row_e, r) < DOMINANCE_RATIO:
        fails.append("row_dominance")
    if dominance(col_e, c) < DOMINANCE_RATIO:
        fails.append("col_dominance")
    twist_db = 10.0 * np.log10((col_e[c] + 1e-12) / (row_e[r] + 1e-12))
    if abs(twist_db) > TWIST_DB_LIMIT:
        fails.append("twist")
    if (row_e[r] + col_e[c]) < SNR_GATE * noise_floor:
        fails.append("snr")
    if (row_e[r] + col_e[c]) < CONCENTRATION_MIN * total_power:
        fails.append("concentration")

    digit = KEYPAD[(r, c)] if not fails else None
    return digit, r, c, fails


def estimate_noise_floor(frames_energy: np.ndarray) -> float:
    """以全部帧双频组最大能量之和的中位数估计噪声底。"""
    pair_power = frames_energy[:, :4].max(axis=1) + frames_energy[:, 4:].max(axis=1)
    return float(np.median(pair_power)) + 1e-12


def refine_boundaries(signal: np.ndarray, digit: str, approx_start: int,
                      approx_end: int, fs=FS, win=64, hop=20,
                      thresh_ratio=0.15):
    """在事件粗定位附近用 8 ms 短窗重扫该数字双音功率,
    得到样本级精确起止点与真实持续时长(ms)。"""
    r, c = DIGIT_TO_PAIR[digit]
    margin = int(0.06 * fs)
    lo = max(0, approx_start - margin)
    hi = min(len(signal), approx_end + margin)
    seg = signal[lo:hi]
    if len(seg) < win:
        return approx_start, approx_end, (approx_end - approx_start) / fs * 1000.0
    t = np.arange(win) / fs
    w = np.hanning(win)
    k1 = np.exp(-2j * np.pi * ROW_FREQS[r] * t)
    k2 = np.exp(-2j * np.pi * COL_FREQS[c] * t)
    n = 1 + (len(seg) - win) // hop
    power = np.empty(n)
    for i in range(n):
        xw = seg[i * hop: i * hop + win] * w
        power[i] = abs(np.dot(xw, k1)) ** 2 + abs(np.dot(xw, k2)) ** 2
    above = np.flatnonzero(power > thresh_ratio * power.max())
    if above.size == 0:
        return approx_start, approx_end, 0.0
    onset = lo + int(above[0]) * hop
    offset = lo + int(above[-1]) * hop + win
    return onset, offset, (offset - onset) / fs * 1000.0


class DurationStateMachine:
    """三状态持续时间状态机: IDLE -> CANDIDATE -> ACTIVE。

    - CANDIDATE: 连续同数字帧累计中, 未达最短时长;
    - ACTIVE:    已达最短时长, 等待静音间隔收尾;
    - 数字在无静音间隔下直接跳变 -> 判重叠, 前后候选均拒识;
    - CANDIDATE 未达时长即结束 -> 判过短, 拒识。
    所有状态迁移均记录 (帧号, 时间, 源状态, 目标状态, 原因)。
    """

    IDLE, CANDIDATE, ACTIVE = "IDLE", "CANDIDATE", "ACTIVE"

    def __init__(self, fs=FS, hop=HOP, frame_len=FRAME_LEN):
        self.fs = fs
        self.hop = hop
        self.frame_len = frame_len
        self.min_frames = max(1, int(np.ceil(MIN_TONE_MS * fs / 1000.0 / hop)))
        self.silence_frames = max(1, int(np.ceil(MIN_SILENCE_MS * fs / 1000.0 / hop)))
        self.state = self.IDLE
        self.cand = None
        self.count = 0
        self.dropout = 0
        self.foreign = 0
        self.start_frame = 0
        self.tainted = False   # 由重叠触发的候选, 最终必须拒识
        self.events: list[Event] = []
        self.transitions: list[dict] = []

    def _log(self, frame_idx, to_state, reason):
        self.transitions.append({
            "frame": frame_idx,
            "time_ms": round(frame_idx * self.hop / self.fs * 1000.0, 1),
            "from": self.state,
            "to": to_state,
            "reason": reason,
        })
        self.state = to_state

    def _finalize(self, end_frame, status, reason):
        if self.cand is None:
            return
        ev = Event(
            digit=self.cand,
            start_sample=self.start_frame * self.hop,
            end_sample=end_frame * self.hop + self.frame_len,
            status=status,
            reason=reason,
            confirmed=self.state == self.ACTIVE,
        )
        ev.transitions = list(self.transitions)
        self.events.append(ev)
        self.cand = None
        self.count = 0
        self.dropout = 0
        self.foreign = 0

    def _begin(self, frame_idx, digit, tainted):
        self.cand = digit
        self.count = 1
        self.dropout = 0
        self.foreign = 0
        self.start_frame = frame_idx
        self.tainted = tainted
        self._log(frame_idx, self.CANDIDATE, f"出现候选数字 {digit}")

    def step(self, frame_idx, label):
        L = label.digit
        if self.state == self.IDLE:
            if L is not None:
                self._begin(frame_idx, L, tainted=False)
            return

        if L == self.cand:
            self.count += 1
            self.dropout = 0
            self.foreign = 0
            if self.state == self.CANDIDATE and self.count >= self.min_frames:
                self._log(frame_idx, self.ACTIVE,
                          f"持续 {self.count * self.hop / self.fs * 1000:.0f} ms 达到最短时长")
            return

        if L is None:
            self.dropout += 1
            self.foreign = 0
            limit = self.silence_frames if self.state == self.ACTIVE else MAX_DROPOUT_FRAMES
            if self.dropout >= limit:
                if self.state == self.ACTIVE and not self.tainted:
                    self._finalize(frame_idx - self.dropout, "accepted", "ok")
                    self._log(frame_idx, self.IDLE, "静音间隔达标, 接受")
                elif self.state == self.ACTIVE and self.tainted:
                    self._finalize(frame_idx - self.dropout, "rejected",
                                   "overlap: 与前一按键无静音间隔")
                    self._log(frame_idx, self.IDLE, "重叠候选, 拒识")
                else:
                    self._finalize(frame_idx - self.dropout, "rejected",
                                   f"too_short: 持续不足 {MIN_TONE_MS:.0f} ms")
                    self._log(frame_idx, self.IDLE, "持续过短, 拒识")
            return

        # L 不是 None 且与当前候选不同: 容忍个别异帧(噪声毛刺),
        # 连续异帧达到门限才判无静音间隔的数字跳变 -> 重叠
        self.foreign += 1
        if self.foreign < FOREIGN_PATIENCE:
            return
        prev = self.cand
        self._finalize(frame_idx - self.foreign + 1, "rejected",
                       f"overlap: 与 {L} 无静音间隔交叠")
        self._log(frame_idx, self.IDLE, f"数字跳变 {prev}->{L}, 判重叠拒识")
        self._begin(frame_idx, L, tainted=True)

    def flush(self, frame_idx):
        if self.state == self.ACTIVE and not self.tainted:
            self._finalize(frame_idx, "accepted", "ok")
        elif self.state == self.ACTIVE:
            self._finalize(frame_idx, "rejected", "overlap: 与前/后按键无静音间隔")
        elif self.state == self.CANDIDATE:
            self._finalize(frame_idx, "rejected",
                           f"too_short: 持续不足 {MIN_TONE_MS:.0f} ms")
        if self.state != self.IDLE:
            self._log(frame_idx, self.IDLE, "录音结束")


def detect(signal: np.ndarray, fs=FS):
    """分帧打标 + 状态机, 返回 (events, frames, transitions, noise_floor)。"""
    n_frames = 1 + max(0, (len(signal) - FRAME_LEN) // HOP)
    energies = np.empty((n_frames, len(ALL_FREQS)))
    totals = np.empty(n_frames)
    for i in range(n_frames):
        seg = signal[i * HOP: i * HOP + FRAME_LEN]
        energies[i] = goertzel_energies(seg, fs=fs)
        totals[i] = 2.0 * float(np.sum(seg ** 2))
    noise_floor = estimate_noise_floor(energies)

    frames: list[FrameLabel] = []
    sm = DurationStateMachine(fs=fs)
    for i in range(n_frames):
        digit, r, c, fails = label_frame(energies[i], noise_floor, totals[i])
        fl = FrameLabel(
            index=i,
            time_ms=i * HOP / fs * 1000.0,
            digit=digit,
            row_freq=ROW_FREQS[r],
            col_freq=COL_FREQS[c],
            energies=energies[i],
            valid=digit is not None,
            fail_checks=fails,
        )
        frames.append(fl)
        sm.step(i, fl)
    sm.flush(n_frames - 1)

    # 子帧级边界精化: 修正起止样本, 并按真实时长复核过短判决
    for e in sm.events:
        onset, offset, dur_ms = refine_boundaries(
            signal, e.digit, e.start_sample, e.end_sample, fs=fs)
        e.start_sample, e.end_sample = onset, offset
        e.duration_ms = dur_ms
        if e.status == "accepted" and dur_ms < MIN_TONE_MS:
            e.status = "rejected"
            e.reason = f"too_short: 精化后实测 {dur_ms:.0f} ms < {MIN_TONE_MS:.0f} ms"
    return sm.events, frames, sm.transitions, noise_floor
