"""分块媒体 XOR 单校验擦除恢复演示。

每组 4 个等长数据块 + 1 个 XOR 校验块，块内携带长度与 SHA-256 摘要，
支持单块缺失/损坏重建，并对双块缺失等不可恢复情形如实报告缺口。
"""

__all__ = ["pcm", "blocks", "store", "damage", "recover", "verify", "cli", "demo"]
