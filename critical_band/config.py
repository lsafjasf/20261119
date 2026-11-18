"""模型与信号处理参数（全部显式给出，便于复核与修改）。"""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class ModelParams:
    """临界带分析所用的全部可调参数。

    频谱归一化与窗补偿
    ------------------
    fs:                 采样率 Hz
    n_fft:              FFT 长度（样本），可与窗长不同，支持零填充
    win_length:         分析窗长度（样本）
    hop_ratio:          帧移占窗长比例（0.5 = 50% 重叠）
    window:             窗类型："hann" | "hamming" | "blackman" | "rect"
    full_scale_spl_db:  数字满量程（0 dBFS 正弦）对应的声压级标定 dB SPL

    绝对听阈（ATH, Terhardt 1979 自由场近似）
    ----------------------------------------
    Tq(f) = 3.64 (f/1000)^-0.8
          + 6.5 exp(-0.6 (f/1000 - 3.3)^2)
          + 1e-3 (f/1000)^4   （单位 dB SPL）

    掩蔽模型（简化 MPEG-1 心理声学模型 1 风格，仅作规则演示）
    -------------------------------------------------------
    tonal_offset_db:    音调（tonal）掩蔽声的掩蔽偏移
    noise_offset_db:    噪声（non-tonal）掩蔽声的掩蔽偏移
    spread_down_db_bark: 向低频扩散斜率 dB/Bark
    spread_up_db_bark:   向高频扩散斜率 dB/Bark
    tonal_prominence_db: 音调判定突出量 dB：候选峰须为局部极大，且相对
                        两侧 ±2 bin 的平均水平至少突出该值（考虑 Hann 窗
                        主瓣泄漏，离-bin 峰的最近邻会很高，故不直接比较）
    """

    fs: float = 48000.0
    n_fft: int = 4096
    win_length: int = 2048
    hop_ratio: float = 0.5
    window: str = "hann"
    full_scale_spl_db: float = 94.0

    tonal_offset_db: float = -6.0
    noise_offset_db: float = -2.3
    spread_down_db_bark: float = 27.0
    spread_up_db_bark: float = 12.0
    tonal_prominence_db: float = 3.0

    def with_changes(self, **kwargs) -> "ModelParams":
        """返回替换若干字段后的新参数对象。"""
        data = asdict(self)
        unknown = set(kwargs) - set(data)
        if unknown:
            raise TypeError(f"未知参数: {sorted(unknown)}")
        data.update(kwargs)
        return ModelParams(**data)

    def summary_lines(self) -> list[str]:
        """生成参数说明文本（英文，写入图内面板，避免缺中文字体）。"""
        return [
            "[Spectrum normalization & window compensation]",
            f"  fs={self.fs:g} Hz, n_fft={self.n_fft}, "
            f"win_length={self.win_length}, hop={self.hop_ratio:g}",
            f"  window={self.window}, 0 dBFS -> {self.full_scale_spl_db:g} dB SPL",
            "  amplitude: |X[k]| / sum(w); power: mean-square normalized",
            "  PSD factor 1/(fs*sum(w^2)); zero-padding keeps band energy",
            "[Masking model parameters]",
            f"  tonal_offset={self.tonal_offset_db} dB, "
            f"noise_offset={self.noise_offset_db} dB",
            f"  spreading: {self.spread_down_db_bark} dB/Bark (down), "
            f"{self.spread_up_db_bark} dB/Bark (up)",
            f"  tonal prominence threshold={self.tonal_prominence_db} dB",
            "  absolute threshold: Terhardt (1979) free-field",
        ]
