"""故障注入：删除块、翻转字节、截断，模拟真实介质/传输故障。"""

from __future__ import annotations

import os

from .blocks import HEADER_SIZE
from .store import block_path


def delete_block(root: str, group: int, slot: int) -> str:
    path = block_path(root, group, slot)
    if not os.path.exists(path):
        raise FileNotFoundError(f"块 {group}:{slot} 已不存在，无法再次删除")
    os.remove(path)
    return path


def flip_byte(root: str, group: int, slot: int, offset: int,
              in_payload: bool = True) -> int:
    """翻转一个字节。

    in_payload=True 时 offset 相对载荷起点（0 起），会自动加上头部偏移；
    否则 offset 为整个块文件的绝对字节偏移。
    """
    path = block_path(root, group, slot)
    if not os.path.exists(path):
        raise FileNotFoundError(f"块 {group}:{slot} 不存在，无法翻转字节")
    with open(path, "rb") as handle:
        blob = bytearray(handle.read())
    target = offset + HEADER_SIZE if in_payload else offset
    if not 0 <= target < len(blob):
        raise IndexError(
            f"偏移越界：绝对位置 {target}，块大小 {len(blob)}"
        )
    blob[target] ^= 0xFF
    with open(path, "wb") as handle:
        handle.write(blob)
    return target


def truncate_block(root: str, group: int, slot: int, keep_bytes: int) -> None:
    path = block_path(root, group, slot)
    if not os.path.exists(path):
        raise FileNotFoundError(f"块 {group}:{slot} 不存在，无法截断")
    with open(path, "r+b") as handle:
        handle.truncate(keep_bytes)
