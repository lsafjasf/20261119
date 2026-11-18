"""Broadcast WAVE (BWF) 解析、核对与安全编辑工具。

只依赖 Python 标准库。音频样本（data chunk）在任何编辑导出后保持字节不变，
未知 chunk 原样保留。
"""

from .chunks import Chunk, WaveFile, WaveFormatError, parse_wave
from .inspect import Finding, Locator, inspect_wave
from .editor import EditError, EditableField, apply_edits

__all__ = [
    "Chunk",
    "WaveFile",
    "WaveFormatError",
    "parse_wave",
    "Finding",
    "Locator",
    "inspect_wave",
    "EditError",
    "EditableField",
    "apply_edits",
]
