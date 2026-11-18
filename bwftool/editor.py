"""可编辑字段目录与导出重建。

重建时严格保持 chunk 顺序，data chunk 与所有未知 chunk 的字节（含奇数
长度 chunk 的补字节）逐字节保留；RIFF 大小按新内容重新计算。
"""

from __future__ import annotations

from dataclasses import dataclass

from .bext import BextData
from .chunks import Chunk, WaveFile
from .inspect import InspectionResult, inspect_wave
from .ixml import EDITABLE_TAGS, IXMLData


class EditError(ValueError):
    """编辑请求非法（只读字段、字段不存在、值超长等）。"""


BEXT_FIXED_EDITABLE = (
    "Description",
    "Originator",
    "OriginatorReference",
    "OriginationDate",
    "OriginationTime",
)


@dataclass(frozen=True)
class EditableField:
    key: str
    chunk_id: str
    field_name: str
    current_value: str


def list_editable_fields(result: InspectionResult) -> list[EditableField]:
    """枚举当前文件真正可编辑的字段（只读字段不在其中）。"""
    catalog: list[EditableField] = []
    if result.bext is not None:
        bext = result.bext
        values = {
            "Description": bext.description,
            "Originator": bext.originator,
            "OriginatorReference": bext.originator_reference,
            "OriginationDate": bext.origination_date,
            "OriginationTime": bext.origination_time,
        }
        for name, value in values.items():
            catalog.append(
                EditableField(f"bext.{name}", "bext", name, value)
            )
        catalog.append(
            EditableField(
                "bext.CodingHistory",
                "bext",
                "CodingHistory",
                bext.coding_history,
            )
        )
    if result.ixml is not None:
        ixml = result.ixml
        present = {
            tag
            for tag in EDITABLE_TAGS
            for element in result.ixml.root.iter()
            if element.tag.rsplit("}", 1)[-1] == tag
        }
        values = {
            "SCENE": ixml.scene,
            "TAKE": ixml.take,
            "TAPE": ixml.tape,
            "NOTE": ixml.note,
            "FAMILY": ixml.family,
            "PROJECT": ixml.project,
        }
        for tag in sorted(present):
            catalog.append(
                EditableField(
                    f"iXML.{tag}",
                    "iXML",
                    tag,
                    values.get(tag)
                    if tag in values
                    else _collect_text(ixml, tag),
                )
            )
    return catalog


def _collect_text(ixml: IXMLData, tag: str) -> str:
    for element in ixml.root.iter():
        if element.tag.rsplit("}", 1)[-1] == tag and element.text:
            return element.text.strip()
    return ""


def _replace_bext_field(bext: BextData, field_name: str, value: str) -> bytes:
    data = bytearray(bext.chunk.data)
    if field_name == "CodingHistory":
        encoded = value.encode("latin-1")
        return bytes(data[:602]) + encoded
    offset, length = bext.field_locator(field_name)
    local_offset = offset - bext.chunk.data_offset
    encoded = bext.encode_fixed_field(field_name, value)
    data[local_offset : local_offset + length] = encoded
    return bytes(data)


def _rebuild(wave: WaveFile, replacements: dict[bytes, bytes]) -> bytes:
    """按原 chunk 顺序重建文件；未列入 replacements 的 chunk 原样复制。"""
    output = bytearray(b"RIFF")
    body = bytearray()
    for chunk in wave.chunks:
        payload = replacements.get(chunk.chunk_id, chunk.data)
        body.extend(chunk.chunk_id)
        body.extend(len(payload).to_bytes(4, "little"))
        body.extend(payload)
        if len(payload) % 2 == 1:
            # 保持与原 chunk 相同的补位约定（奇数长度追加 0x00）。
            body.extend(b"\x00")
    output.extend((len(body) + 4).to_bytes(4, "little"))
    output.extend(b"WAVE")
    output.extend(body)
    return bytes(output)


def apply_edits(
    source: bytes | str | InspectionResult, edits: dict[str, str]
) -> bytes:
    """应用点名字段编辑并返回新 WAVE 字节。

    键形如 ``bext.Description``、``iXML.TAKE``。fmt/data/时间参考等
    只读字段无法通过此接口修改。
    """
    if isinstance(source, InspectionResult):
        result = source
    else:
        result = inspect_wave(source)

    replacements: dict[bytes, bytes] = {}
    for key, value in edits.items():
        if "." not in key:
            raise EditError(
                f"编辑键 {key!r} 格式应为 'chunk.字段'，例如 iXML.TAKE"
            )
        chunk_name, field_name = key.split(".", 1)
        if chunk_name == "bext":
            if result.bext is None:
                raise EditError("文件没有 bext chunk")
            allowed = set(BEXT_FIXED_EDITABLE) | {"CodingHistory"}
            if field_name not in allowed:
                raise EditError(
                    f"bext.{field_name} 为只读或不存在；可编辑字段: "
                    f"{', '.join(sorted(allowed))}"
                )
            try:
                replacements[b"bext"] = _replace_bext_field(
                    result.bext, field_name, value
                )
            except (KeyError, ValueError) as exc:
                raise EditError(str(exc)) from exc
        elif chunk_name == "iXML":
            if result.ixml is None:
                raise EditError("文件没有 iXML chunk，无法编辑 iXML 字段")
            try:
                result.ixml.set_leaf_text(field_name, value)
            except (KeyError, ValueError) as exc:
                raise EditError(str(exc)) from exc
            replacements[b"iXML"] = result.ixml.serialize()
        elif chunk_name in ("fmt", "data"):
            raise EditError(
                f"{chunk_name} chunk 为只读：原始音频参数与样本不可修改"
            )
        else:
            raise EditError(
                f"未知 chunk {chunk_name!r} 不允许编辑，只能原样保留"
            )

    return _rebuild(result.wave, replacements)
