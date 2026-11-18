"""iXML chunk 解析、字段定位与安全重序列化。

依据 iXML 1.x/2.0 规范的常见标签集合。解析对命名空间不敏感；定位基于
原始字节扫描，重序列化使用 ElementTree，保证 XML 始终合法。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .chunks import Chunk

# 允许编辑的简单叶子标签（白名单）。时间参考类字段故意不在其中。
EDITABLE_TAGS: frozenset[str] = frozenset(
    {
        "SCENE",
        "TAKE",
        "TAPE",
        "NOTE",
        "FAMILY",
        "PROJECT",
        "TITLE",
        "TRACK_NAME",
        "FILE_NAME",
    }
)


def _local_name(tag: str) -> str:
    """去掉 XML 命名空间前缀，返回本地标签名。"""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _findtext(root: ET.Element, name: str) -> str | None:
    for element in root.iter():
        if _local_name(element.tag) == name and element.text:
            return element.text.strip()
    return None


def _find_int(root: ET.Element, name: str) -> int | None:
    text = _findtext(root, name)
    if text is None:
        return None
    try:
        return int(text.strip())
    except ValueError:
        return None


def _find_float(root: ET.Element, name: str) -> float | None:
    text = _findtext(root, name)
    if text is None:
        return None
    try:
        return float(text.strip())
    except ValueError:
        return None


@dataclass
class IXMLData:
    chunk: Chunk
    root: ET.Element
    declaration: bytes
    scene: str | None
    take: str | None
    tape: str | None
    note: str | None
    project: str | None
    family: str | None
    take_type: str | None
    track_count: int | None
    sample_rate: float | None
    bit_depth: int | None
    file_sample_width: int | None
    file_channel_count: int | None
    take_timestamp: str | None
    bwf_start_time_offset_samples: int | None
    track_names: list[str] = field(default_factory=list)

    @classmethod
    def decode(cls, chunk: Chunk) -> "IXMLData":
        raw = chunk.data
        declaration = b""
        match = re.match(rb"\s*<\?xml[^?]*\?>", raw)
        if match:
            declaration = raw[: match.end()]
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise ValueError(f"iXML 不是合法 XML: {exc}") from exc
        track_names = [
            element.text.strip()
            for element in root.iter()
            if _local_name(element.tag) == "TRACK_NAME" and element.text
        ]
        return cls(
            chunk=chunk,
            root=root,
            declaration=declaration,
            scene=_findtext(root, "SCENE"),
            take=_findtext(root, "TAKE"),
            tape=_findtext(root, "TAPE"),
            note=_findtext(root, "NOTE"),
            project=_findtext(root, "PROJECT"),
            family=_findtext(root, "FAMILY"),
            take_type=_findtext(root, "TAKE_TYPE"),
            track_count=_find_int(root, "TRACK_COUNT"),
            sample_rate=_find_float(root, "SAMPLE_RATE"),
            bit_depth=_find_int(root, "BIT_DEPTH"),
            file_sample_width=_find_int(root, "FILE_SAMPLE_WIDTH"),
            file_channel_count=_find_int(root, "FILE_CHANNEL_COUNT"),
            take_timestamp=_findtext(root, "TAKE_TIMESTAMP"),
            bwf_start_time_offset_samples=_find_int(
                root, "BWF_START_TIME_OFFSET_SAMPLES"
            ),
            track_names=track_names,
        )

    def _element(self, name: str) -> ET.Element:
        for element in self.root.iter():
            if _local_name(element.tag) == name:
                return element
        raise KeyError(f"iXML 中不存在标签 <{name}>")

    def tag_locator(self, name: str) -> tuple[int, int]:
        """在原始字节中定位标签，返回 (文件偏移, 元素整体字节长度)。"""
        self._element(name)
        open_pattern = re.compile(
            (r"<(?:[\w.-]+:)?" + re.escape(name) + r"\b[^>]*>").encode()
        )
        open_match = open_pattern.search(self.chunk.data)
        if open_match is None:
            raise KeyError(f"无法在 iXML 字节中定位 <{name}>")
        close_pattern = re.compile(
            (
                r"</(?:[\w.-]+:)?"
                + re.escape(name)
                + r"\s*>"
            ).encode()
        )
        close_match = close_pattern.search(
            self.chunk.data, open_match.end()
        )
        local_end = (
            close_match.end() if close_match else open_match.end()
        )
        return (
            self.chunk.data_offset + open_match.start(),
            local_end - open_match.start(),
        )

    def set_leaf_text(self, name: str, value: str) -> None:
        """仅允许修改白名单内、无属性、无子元素的叶子标签文本。"""
        if name not in EDITABLE_TAGS:
            raise ValueError(
                f"iXML.{name} 不是可编辑字段（白名单: "
                f"{', '.join(sorted(EDITABLE_TAGS))}）"
            )
        element = self._element(name)
        if list(element) or element.attrib:
            raise ValueError(
                f"iXML.{name} 含子元素或属性，拒绝就地修改"
            )
        value.encode("utf-8")
        element.text = value

    def serialize(self) -> bytes:
        """重序列化为 UTF-8 XML，尽量保留原 XML 声明。"""
        body = ET.tostring(self.root, encoding="utf-8")
        if self.declaration:
            return self.declaration.rstrip() + b"\n" + body
        return b'<?xml version="1.0" encoding="UTF-8"?>\n' + body
