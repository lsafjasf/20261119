"""听觉临界带（Bark）分析工具包。

本工具按显式给出的 24 个 Zwicker 临界带划分短时频谱，实现一个
简化的音调/噪声掩蔽阈值模型，并计算可听余量（audible margin）。

注意：所有结果仅为所实现信号处理规则的工程演示，不构成任何医学
或临床听力结论。
"""

from .bark import BARK_BANDS, bark_from_freq, freq_from_bark, assign_band
from .config import ModelParams
from .signals import make_signal
from .analysis import run_analysis, AnalysisResult

__all__ = [
    "BARK_BANDS",
    "bark_from_freq",
    "freq_from_bark",
    "assign_band",
    "ModelParams",
    "make_signal",
    "run_analysis",
    "AnalysisResult",
]

__version__ = "0.1.0"
