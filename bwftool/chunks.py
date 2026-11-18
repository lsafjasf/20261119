"""RIFF/WAVE 容器层解析：枚举所有 chunk，保留原始字节与补字节信息。"""

from __future__ import annotations

from dataclasses import dataclass, field


class WaveFormatError(ValueError):
    """文件不是受支持的 RIFF/WAVE 容器，或结构被截断。"""


@dataclass
class Chunk:
    """单个 RIFF chunk 的定位信息与数据快照。"""

    chunk_id: bytes
    header_offset: int
    data_offset: int
    size: int
    pad: int
    data: bytes

    @property
    def end_offset(self) -> int:
        """chunk 最后一个字节的下一个偏移（含补字节）。"""
        return self.data_offset + self.size + self.pad

    @property
    def is_odd(self) -> bool:
        """数据长度是否为奇数（BWF 允许，后随一个 0x00 补字节）。"""
        return self.size % 2 == 1


@dataclass
class FmtChunk:
    """fmt chunk 的解码结果（支持 WAVE_FORMAT_EXTENSIBLE）。"""

    format_tag: int
    channels: int
    sample_rate: int
    avg_bytes_per_sec: int
    block_align: int
    bits_per_sample: int
    extension_size: int | None = None
    valid_bits_per_sample: int | None = None
    channel_mask: int | None = None
    subformat_guid: bytes | None = None
    raw: bytes = b""

    @property
    def effective_format_tag(self) -> int:
        """EXTENSIBLE 时返回子格式 GUID 里的格式码，否则返回原格式码。"""
        if self.format_tag == 0xFFFE and self.subformat_guid:
            return int.from_bytes(self.subformat_guid[:2], "little")
        return self.format_tag

    def describe_sample_type(self) -> str:
        """人类可读的采样类型描述，供与 iXML 核对。"""
        tag = self.effective_format_tag
        if tag == 0x0001:
            kind = "PCM integer"
        elif tag == 0x0003:
            kind = "IEEE float"
        else:
            kind = f"format tag 0x{tag:04X}"
        return f"{kind}, {self.bits_per_sample}-bit, {self.channels}ch"


@dataclass
class WaveFile:
    """已解析的 WAVE 文件：原始字节、chunk 列表与便捷访问器。"""

    path: str | None
    raw: bytes
    riff_declared_size: int
    riff_actual_size: int
    chunks: list[Chunk] = field(default_factory=list)

    def chunk(self, chunk_id: bytes) -> Chunk | None:
        for chunk in self.chunks:
            if chunk.chunk_id == chunk_id:
                return chunk
        return None

    def has(self, chunk_id: bytes) -> bool:
        return self.chunk(chunk_id) is not None

    @property
    def data_chunk(self) -> Chunk | None:
        return self.chunk(b"data")

    def decode_fmt(self) -> FmtChunk:
        chunk = self.chunk(b"fmt ")
        if chunk is None:
            raise WaveFormatError("缺少 fmt chunk")
        data = chunk.data
        if len(data) < 16:
            raise WaveFormatError("fmt chunk 长度不足 16 字节")
        fmt = FmtChunk(
            format_tag=int.from_bytes(data[0:2], "little"),
            channels=int.from_bytes(data[2:4], "little"),
            sample_rate=int.from_bytes(data[4:8], "little"),
            avg_bytes_per_sec=int.from_bytes(data[8:12], "little"),
            block_align=int.from_bytes(data[12:14], "little"),
            bits_per_sample=int.from_bytes(data[14:16], "little"),
            raw=data,
        )
        if len(data) >= 18:
            fmt.extension_size = int.from_bytes(data[16:18], "little")
        if fmt.format_tag == 0xFFFE and len(data) >= 40:
            fmt.valid_bits_per_sample = int.from_bytes(data[18:20], "little")
            fmt.channel_mask = int.from_bytes(data[20:24], "little")
            fmt.subformat_guid = data[24:40]
        return fmt


def parse_wave(source: bytes | str) -> WaveFile:
    """解析 WAVE 字节串或文件路径，保留全部 chunk（含未知与奇数长度 chunk）。"""
    if isinstance(source, str):
        with open(source, "rb") as handle:
            raw = handle.read()
        path = source
    else:
        raw = bytes(source)
        path = None

    if len(raw) < 12:
        raise WaveFormatError("文件长度不足 12 字节，不可能是 RIFF/WAVE")
    if raw[0:4] != b"RIFF":
        if raw[0:4] in (b"RF64", b"BW64"):
            raise WaveFormatError("RF64/BW64 64 位容器暂不支持，请先转换为普通 RIFF/WAVE")
        raise WaveFormatError(f"缺少 RIFF 标识，实际为 {raw[0:4]!r}")
    if raw[8:12] != b"WAVE":
        raise WaveFormatError(f"RIFF 表单类型不是 WAVE，实际为 {raw[8:12]!r}")

    riff_declared_size = int.from_bytes(raw[4:8], "little")
    riff_actual_size = len(raw) - 8

    chunks: list[Chunk] = []
    offset = 12
    total = len(raw)
    while offset < total:
        if total - offset < 8:
            raise WaveFormatError(f"偏移 {offset} 处 chunk 头被截断")
        chunk_id = raw[offset : offset + 4]
        size = int.from_bytes(raw[offset + 4 : offset + 8], "little")
        data_offset = offset + 8
        data_end = data_offset + size
        if data_end > total:
            raise WaveFormatError(
                f"chunk {chunk_id!r} 声明 {size} 字节但文件在偏移 {data_end} 处截断"
            )
        pad = 0
        if size % 2 == 1:
            if data_end >= total:
                raise WaveFormatError(
                    f"奇数长度 chunk {chunk_id!r} 缺少补字节"
                )
            if raw[data_end] != 0x00:
                raise WaveFormatError(
                    f"奇数长度 chunk {chunk_id!r} 的补字节不是 0x00，"
                    f"实际为 0x{raw[data_end]:02X}"
                )
            pad = 1
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                header_offset=offset,
                data_offset=data_offset,
                size=size,
                pad=pad,
                data=raw[data_offset:data_end],
            )
        )
        offset = data_end + pad

    return WaveFile(
        path=path,
        raw=raw,
        riff_declared_size=riff_declared_size,
        riff_actual_size=riff_actual_size,
        chunks=chunks,
    )
