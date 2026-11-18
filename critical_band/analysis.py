"""端到端分析流程：信号 -> 短时频谱 -> 临界带能量 -> 掩蔽阈值/可听余量。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .bark import BARK_BANDS, BAND_CENTER
from .config import ModelParams
from .spectrum import stft, SpectrumFrames
from .masking import compute_thresholds


@dataclass
class AnalysisResult:
    """完整分析结果。"""

    params: ModelParams
    signal: np.ndarray
    frames: SpectrumFrames
    mean_power_db: np.ndarray       # 帧平均后的 bin 功率谱 (dBFS)
    mean_band_power: np.ndarray     # 帧平均后的 24 带均方能量（线性）
    band_level_db_spl: np.ndarray   # 24 带信号水平 dB SPL
    thresholds: dict

    @property
    def band_centers(self) -> np.ndarray:
        return BAND_CENTER

    @property
    def band_indices(self) -> np.ndarray:
        return np.arange(1, len(BARK_BANDS) + 1)


def run_analysis(signal: np.ndarray, params: ModelParams) -> AnalysisResult:
    """对给定信号执行完整临界带分析。

    多帧按功率平均（50% 重叠 Hann 窗满足 COLA，平均谱代表信号稳态
    功率），掩蔽模型在帧平均谱上运行一次。
    """
    frames = stft(signal, params)

    mean_power = np.mean(frames.power_spec, axis=0)
    mean_band_power = np.mean(frames.band_power, axis=0)
    mean_power_db = 10.0 * np.log10(mean_power + 1e-20)

    thresholds = compute_thresholds(mean_power_db, frames.freqs, params)
    band_level = 10.0 * np.log10(mean_band_power + 1e-20) \
        + params.full_scale_spl_db

    return AnalysisResult(
        params=params,
        signal=np.asarray(signal, dtype=float),
        frames=frames,
        mean_power_db=mean_power_db,
        mean_band_power=mean_band_power,
        band_level_db_spl=band_level,
        thresholds=thresholds,
    )


def band_energy_invariance(signal: np.ndarray, params: ModelParams,
                           n_fft_list=(1024, 2048, 4096, 8192)
                           ) -> dict:
    """同一信号在不同 FFT 长度下的带内能量一致性检查。

    固定窗长，仅改变 n_fft（含零填充）。返回各 n_fft 的带能量矩阵
    以及相对参考值的最大绝对偏差（dB）。
    """
    energies = []
    used = []
    for n_fft in n_fft_list:
        if n_fft < params.win_length:
            continue
        p = params.with_changes(n_fft=n_fft)
        r = run_analysis(signal, p)
        energies.append(r.mean_band_power)
        used.append(n_fft)
    mat = np.vstack(energies)
    ref = mat[0]
    valid = ref > 1e-12
    diff_db = np.abs(10.0 * np.log10(mat[:, valid] / ref[valid]))
    return {
        "n_fft_list": used,
        "band_power": mat,
        "max_abs_diff_db": float(np.max(diff_db)),
        "mean_abs_diff_db": float(np.mean(diff_db)),
    }
