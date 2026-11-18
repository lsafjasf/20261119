"""块格式与 XOR 运算。

块文件布局（little-endian）::

    magic(4)="XORB" | version(1) | group u32 | slot u8
    | payload_len u32 | sha256(32) | payload(payload_len)

摘要覆盖固定前缀、组号、槽位、长度与载荷，因此长度字段或载荷被篡改
都会导致校验失败；槽位 0..3 为数据块，槽位 4 为校验块。
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

MAGIC = b"XORB"
VERSION = 1
DATA_SLOTS = 4
PARITY_SLOT = 4
SLOT_COUNT = 5
DIGEST_LEN = 32
HEADER_SIZE = 4 + 1 + 4 + 1 + 4 + DIGEST_LEN


def digest_payload(group: int, slot: int, payload: bytes) -> bytes:
    h = hashlib.sha256()
    h.update(b"xor-block-v1")
    h.update(struct.pack("<IB", group, slot))
    h.update(struct.pack("<I", len(payload)))
    h.update(payload)
    return h.digest()


def build_block(group: int, slot: int, payload: bytes) -> bytes:
    header = MAGIC + struct.pack(
        "<BIBI", VERSION, group, slot, len(payload)
    ) + digest_payload(group, slot, payload)
    return header + payload


@dataclass
class ParsedBlock:
    group: int
    slot: int
    payload_len: int
    digest: bytes
    payload: bytes
    valid: bool
    reason: str = ""


def parse_block(blob: bytes) -> ParsedBlock:
    """解析并校验单个块；任何结构或摘要问题都标记为无效并附原因。"""
    if len(blob) < HEADER_SIZE:
        return ParsedBlock(-1, -1, 0, b"", b"", False, "块长度小于头部，文件可能被截断")
    magic = blob[:4]
    if magic != MAGIC:
        return ParsedBlock(-1, -1, 0, b"", b"", False, "magic 不匹配，不是本演示的块文件")
    version, group, slot, payload_len = struct.unpack("<BIBI", blob[4:14])
    if version != VERSION:
        return ParsedBlock(group, slot, payload_len, b"", b"", False, f"不支持的版本 {version}")
    digest = blob[14:14 + DIGEST_LEN]
    if len(blob) != HEADER_SIZE + payload_len:
        return ParsedBlock(group, slot, payload_len, digest, b"",
                           False, "声明长度与文件实际字节数不一致（截断或追加）")
    payload = blob[HEADER_SIZE:]
    if digest != digest_payload(group, slot, payload):
        return ParsedBlock(group, slot, payload_len, digest, payload,
                           False, "SHA-256 摘要不匹配，载荷或元数据已被篡改")
    return ParsedBlock(group, slot, payload_len, digest, payload, True, "摘要校验通过")


def xor_bytes(left: bytes, right: bytes) -> bytes:
    if len(left) != len(right):
        raise ValueError(f"XOR 要求等长，实际为 {len(left)} 与 {len(right)}")
    return bytes(a ^ b for a, b in zip(left, right))


def parity_of(payloads: list[bytes]) -> bytes:
    if len(payloads) != DATA_SLOTS:
        raise ValueError(f"校验块由 {DATA_SLOTS} 个数据块 XOR 得到")
    length = len(payloads[0])
    if any(len(p) != length for p in payloads):
        raise ValueError("组内数据块必须等长")
    result = bytes(length)
    for payload in payloads:
        result = xor_bytes(result, payload)
    return result
