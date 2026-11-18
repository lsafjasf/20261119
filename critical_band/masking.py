"""简化的音调/噪声掩蔽阈值模型（MPEG-1 心理声学模型 1 风格）。

实现的规则（全部参数见 ModelParams，仅作工程演示，非医学听力评估）

1. 绝对听阈 ATH 采用 Terhardt (1979) 自由场近似：
       Tq(f) = 3.64 (f/kHz)^-0.8
             + 6.5 exp(-0.6 (f/kHz - 3.3)^2)
             + 1e-3 (f/kHz)^4                     [dB SPL]
2. 音调掩蔽声：候选 bin 为局部极大（高于左右紧邻 bin），相对两侧
   ±2、±3 bin 四点的平均功率至少突出 tonal_prominence_db，且其
   dB SPL 水平高于该频率的绝对听阈（抑制矩形窗高旁瓣假峰）。避开
   ±1 紧邻 bin，是因为离-bin 音调受窗主瓣泄漏影响，紧邻 bin 也很高；
   该口径对 Hann/Hamming/Blackman/矩形窗均适用。
3. 噪声掩蔽声：每个临界带扣除音调分量后的剩余带能量。
4. 掩蔽偏移：音调 tonal_offset_db（典型 -6 dB），
   噪声 noise_offset_db（典型 -2.3 dB）。
5. 扩散函数（Bark 域分段线性）：
       dz < 0 : 斜率 spread_down_db_bark（向低频，较陡）
       dz >= 0: 斜率 spread_up_db_bark（向高频，较缓）
6. 每个临界带的全局阈值 = ATH 与所有掩蔽声贡献的能量和
   （dB 量先转线性功率再相加）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .bark import BARK_BANDS, BAND_CENTER, bark_from_freq


def absolute_threshold_db_spl(freq_hz: np.ndarray) -> np.ndarray:
    """Terhardt (1979) 自由场绝对听阈，返回 dB SPL。"""
    f = np.maximum(np.asarray(freq_hz, dtype=float), 1e-6)
    fk = f / 1000.0
    return (3.64 * fk ** (-0.8)
            + 6.5 * np.exp(-0.6 * (fk - 3.3) ** 2)
            + 1e-3 * fk ** 4)


@dataclass
class Masker:
    """单个掩蔽声（音调或噪声）。"""

    kind: str           # "tonal" | "noise"
    freq_hz: float
    bark: float
    level_db_spl: float
    band_index: int     # 1..24


def _db_to_power(db: np.ndarray) -> np.ndarray:
    return 10.0 ** (db / 10.0)


def find_tonal_maskers(power_db: np.ndarray, freqs: np.ndarray,
                       prominence_db: float, spl_cal_db: float,
                       window_name: str = "hann") -> list[Masker]:
    """识别音调（局部突出谱峰）掩蔽声。"""
    tonal: list[Masker] = []
    n = len(power_db)
    uppers = np.array([b.upper for b in BARK_BANDS])
    ath_bin = absolute_threshold_db_spl(freqs)
    # 矩形窗旁瓣高且平坦（约 -13 dB），需要更严格的突出量才能避免假峰
    effective_prominence = 10.0 if window_name.lower() == "rect" \
        else prominence_db
    candidates: list[Masker] = []
    for k in range(3, n - 3):
        center = power_db[k]
        is_local_max = center > power_db[k - 1] and center > power_db[k + 1]
        flank_mean = 0.25 * (power_db[k - 3] + power_db[k - 2]
                             + power_db[k + 2] + power_db[k + 3])
        level_spl = center + spl_cal_db
        if (is_local_max and (center - flank_mean) >= effective_prominence
                and level_spl > ath_bin[k]):
            # 峰及左右各一个 bin 合并为该音调的能量
            lo = max(1, k - 1)
            hi = min(n - 1, k + 1)
            level_dbfs = 10 * np.log10(np.sum(_db_to_power(power_db[lo:hi + 1]))
                                       + 1e-20)
            band_idx = int(np.searchsorted(uppers, freqs[k], side="left") + 1)
            band_idx = int(np.clip(band_idx, 1, len(BARK_BANDS)))
            candidates.append(Masker(
                kind="tonal",
                freq_hz=float(freqs[k]),
                bark=float(bark_from_freq(freqs[k])),
                level_db_spl=level_dbfs + spl_cal_db,
                band_index=band_idx,
            ))
    if not candidates:
        return candidates
    # 矩形窗等场景的旁瓣假峰远弱于主峰：剔除比最强候选低 20 dB 以上者。
    # 阈值 20 dB 远小于演示信号中近邻双音的典型电平差，双音仍可同时保留。
    strongest = max(m.level_db_spl for m in candidates)
    return [m for m in candidates
            if strongest - m.level_db_spl <= 20.0]


def build_noise_maskers(power_db: np.ndarray, freqs: np.ndarray,
                        tonal_maskers: list[Masker],
                        spl_cal_db: float) -> list[Masker]:
    """每个临界带去掉音调 bin 后的能量作为噪声掩蔽声。"""
    uppers = np.array([b.upper for b in BARK_BANDS])
    tonal_bins = set()
    for m in tonal_maskers:
        tonal_bins.add(int(np.argmin(np.abs(freqs - m.freq_hz))))

    noise: list[Masker] = []
    for bi, band in enumerate(BARK_BANDS):
        in_band = (freqs >= band.lower) & (freqs < band.upper)
        bins = np.where(in_band)[0]
        bins = np.array([k for k in bins if k not in tonal_bins
                         and 0 < k < len(freqs) - 1])
        if bins.size == 0:
            continue
        level_dbfs = 10 * np.log10(np.sum(_db_to_power(power_db[bins]))
                                   + 1e-20)
        noise.append(Masker(
            kind="noise",
            freq_hz=float(band.center),
            bark=float(bark_from_freq(band.center)),
            level_db_spl=level_dbfs + spl_cal_db,
            band_index=bi + 1,
        ))
    return noise


def spreading_contribution(masker: Masker, target_bark: np.ndarray,
                           params) -> np.ndarray:
    """单个掩蔽声经 Bark 域扩散后在目标频带处的掩蔽水平（dB SPL）。"""
    offset = (params.tonal_offset_db if masker.kind == "tonal"
              else params.noise_offset_db)
    dz = target_bark - masker.bark
    slope = np.where(dz < 0.0,
                     params.spread_down_db_bark,
                     params.spread_up_db_bark)
    return masker.level_db_spl + offset - slope * np.abs(dz)


def compute_thresholds(power_db: np.ndarray, freqs: np.ndarray,
                       params) -> dict:
    """计算 24 个临界带的掩蔽阈值与可听余量。

    返回字典含：ath_db、global_threshold_db、maskers、signal_level_db、
    audible_margin_db（均为长度 24 的 dB SPL 数组，maskers 除外）。
    """
    target_bark = bark_from_freq(BAND_CENTER)
    ath = absolute_threshold_db_spl(BAND_CENTER)

    tonal = find_tonal_maskers(power_db, freqs,
                               params.tonal_prominence_db,
                               params.full_scale_spl_db,
                               window_name=params.window)
    noise = build_noise_maskers(power_db, freqs, tonal,
                                params.full_scale_spl_db)
    maskers = tonal + noise

    # 阈值的能量叠加：先转线性功率求和
    threshold_power = _db_to_power(ath)
    for masker in maskers:
        contrib_db = spreading_contribution(masker, target_bark, params)
        threshold_power += _db_to_power(contrib_db)
    global_threshold = 10.0 * np.log10(threshold_power + 1e-20)

    # 每带信号水平（dB SPL）
    band_power_linear = np.zeros(len(BARK_BANDS))
    for bi, band in enumerate(BARK_BANDS):
        in_band = (freqs >= band.lower) & (freqs < band.upper)
        band_power_linear[bi] = np.sum(
            _db_to_power(power_db[in_band])) if np.any(in_band) else 1e-20
    signal_level = 10.0 * np.log10(band_power_linear + 1e-20) \
        + params.full_scale_spl_db

    # 可听余量：信号超出阈值多少 dB；负值表示低于阈值（被掩蔽/不可闻）
    audible_margin = signal_level - global_threshold

    return {
        "ath_db": ath,
        "global_threshold_db": global_threshold,
        "signal_level_db": signal_level,
        "audible_margin_db": audible_margin,
        "maskers": maskers,
        "tonal_maskers": tonal,
        "noise_maskers": noise,
    }
