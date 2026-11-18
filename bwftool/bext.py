"""bext（Broadcast Audio Extension）chunk 解码与字段级定位。

布局依据 EBU Tech 3285 / SMPTE 标准。定长字符串均为单字节编码、以 NUL 结尾。
"""

from __future__ import annotations

from dataclasses import dataclass

from .chunks import Chunk

# (字段名, 局部偏移, 长度)
_FIXED_FIELDS: tuple[tuple[str, int, int], ...] = (
    ("Description", 0, 256),
    ("Originator", 256, 32),
    ("OriginatorReference", 288, 32),
    ("OriginationDate", 320, 10),
    ("OriginationTime", 330, 8),
    # TimeReference 8 字节（低 32 位 + 高 32 位），偏移 338
    ("TimeReference", 338, 8),
    ("Version", 346, 2),
    ("UMID", 348, 64),
)

TIME_REFERENCE_OFFSET = 338
VERSION_OFFSET = 346
UMID_OFFSET = 348


def _decode_cstr(raw: bytes) -> str:
    """bext 定长字符串：截到首个 NUL，按 latin-1 安全解码（不抛异常）。"""
    return raw.split(b"\x00", 1)[0].decode("latin-1")


@dataclass
class BextData:
    chunk: Chunk
    description: str
    originator: str
    originator_reference: str
    origination_date: str
    origination_time: str
    time_reference: int
    version: int
    umid: bytes
    coding_history: str

    @classmethod
    def decode(cls, chunk: Chunk) -> "BextData":
        data = chunk.data
        if len(data) < 602:
            raise ValueError(
                f"bext chunk 至少需要 602 字节，实际 {len(data)} 字节"
            )
        values = {
            name: _decode_cstr(data[off : off + length])
            for name, off, length in _FIXED_FIELDS
            if name != "TimeReference" and name != "Version"
        }
        time_reference = int.from_bytes(
            data[TIME_REFERENCE_OFFSET : TIME_REFERENCE_OFFSET + 8], "little"
        )
        version = int.from_bytes(
            data[VERSION_OFFSET : VERSION_OFFSET + 2], "little"
        )
        coding_history = data[602:].decode("latin-1")
        return cls(
            chunk=chunk,
            description=values["Description"],
            originator=values["Originator"],
            originator_reference=values["OriginatorReference"],
            origination_date=values["OriginationDate"],
            origination_time=values["OriginationTime"],
            time_reference=time_reference,
            version=version,
            umid=data[UMID_OFFSET : UMID_OFFSET + 64],
            coding_history=coding_history,
        )

    def field_locator(self, name: str) -> tuple[int, int]:
        """返回字段在整个文件中的 (起始偏移, 长度)，用于可点击定位。"""
        for field_name, off, length in _FIXED_FIELDS:
            if field_name == name:
                return self.chunk.data_offset + off, length
        if name == "CodingHistory":
            return self.chunk.data_offset + 602, len(self.chunk.data) - 602
        raise KeyError(f"bext 中没有字段 {name!r}")

    def encode_fixed_field(self, name: str, value: str) -> bytes:
        """把新值编码回定长槽位；超长直接报错（绝不截断元数据）。"""
        for field_name, _, length in _FIXED_FIELDS:
            if field_name == name:
                encoded = value.encode("latin-1")
                if len(encoded) >= length:
                    raise ValueError(
                        f"bext.{name} 槽位 {length - 1} 字节，"
                        f"新值需要 {len(encoded)} 字节（需保留 NUL 结尾）"
                    )
                return encoded.ljust(length, b"\x00")
        raise KeyError(f"bext 中没有可编辑字段 {name!r}")
