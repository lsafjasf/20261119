"""滞回状态机 VAD：能量双阈值 + ZCR/平坦度键盘门控 + 最短持续时间 + 短停顿合并。"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np

from .features import FrameFeatures, extract


@dataclass
class VadParams:
    # 分帧
    frame_ms: float = 25.0
    hop_ms: float = 10.0
    # 噪声底估计：帧能量分位数
    noise_percentile: float = 20.0
    # 滞回双阈值（相对噪声底，dB）：进入阈值 > 退出阈值
    enter_offset_db: float = 10.0
    exit_offset_db: float = 6.0
    # 键盘/宽带瞬态门控：ZCR 与频谱平坦度同时偏高视为敲击样帧
    zcr_hi: float = 0.25
    flat_hi: float = 0.45
    # 状态机时间常数
    min_onset_ms: float = 200.0    # 触发开始所需连续活动时长（键盘单击无法触发）
    hangover_ms: float = 300.0     # 语音内部允许的最大低能量间隔
    merge_gap_ms: float = 400.0    # 短于该间隔的相邻段合并（短停顿合并）
    min_speech_ms: float = 250.0   # 确认段的最短持续时间

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Segment:
    start: float
    end: float

    @property
    def dur(self) -> float:
        return self.end - self.start


@dataclass
class VadResult:
    segments: list[Segment]
    params: VadParams
    noise_db: float
    enter_db: float
    exit_db: float
    states: list[str] = field(default_factory=list)  # 每帧状态（诊断用）


def detect(feats: FrameFeatures, params: VadParams) -> VadResult:
    le = feats.log_energy
    hop_s = feats.hop_ms / 1000.0
    noise_db = float(np.percentile(le, params.noise_percentile))
    enter_db = noise_db + params.enter_offset_db
    exit_db = noise_db + params.exit_offset_db

    click_like = (feats.zcr > params.zcr_hi) & (feats.flatness > params.flat_hi)
    onset_ok = (le > enter_db) & ~click_like
    continue_ok = le > exit_db

    min_onset_s = params.min_onset_ms / 1000.0
    hangover_s = params.hangover_ms / 1000.0

    segments: list[Segment] = []
    states: list[str] = []
    state = "SILENCE"
    onset_count = 0
    onset_start_t = 0.0
    seg_start = 0.0
    last_active_t = 0.0
    inactive = 0.0

    for i in range(len(feats.times)):
        t = float(feats.times[i])
        if state == "SILENCE":
            if onset_ok[i]:
                state = "ONSET"
                onset_count = 1
                onset_start_t = t
        elif state == "ONSET":
            if onset_ok[i]:
                onset_count += 1
                if onset_count * hop_s >= min_onset_s:
                    state = "SPEECH"
                    seg_start = onset_start_t
                    last_active_t = t
                    inactive = 0.0
            else:
                state = "SILENCE"  # 瞬态（如键盘单击）未达到最短触发时长
                onset_count = 0
        else:  # SPEECH
            if continue_ok[i]:
                last_active_t = t
                inactive = 0.0
            else:
                inactive += hop_s
                if inactive >= hangover_s:
                    segments.append(Segment(seg_start, last_active_t + hop_s / 2))
                    state = "SILENCE"
                    onset_count = 0
        states.append(state)

    if state == "SPEECH":
        segments.append(Segment(seg_start, last_active_t + hop_s / 2))

    segments = _merge_and_filter(segments, params)
    return VadResult(segments, params, noise_db, enter_db, exit_db, states)


def _merge_and_filter(segments: list[Segment], params: VadParams) -> list[Segment]:
    if not segments:
        return []
    merge_gap = params.merge_gap_ms / 1000.0
    min_dur = params.min_speech_ms / 1000.0
    merged = [Segment(segments[0].start, segments[0].end)]
    for seg in segments[1:]:
        last = merged[-1]
        if seg.start - last.end <= merge_gap:
            last.end = max(last.end, seg.end)
        else:
            merged.append(Segment(seg.start, seg.end))
    return [s for s in merged if s.dur >= min_dur]


def detect_waveform(x: np.ndarray, sr: int, params: VadParams | None = None) -> VadResult:
    params = params or VadParams()
    feats = extract(x, sr, params.frame_ms, params.hop_ms)
    return detect(feats, params)
