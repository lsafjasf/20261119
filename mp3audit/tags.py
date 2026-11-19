"""ID3 and APEv2 boundary detection around an MPEG audio stream."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TagInfo:
    kind: str
    offset: int
    size: int
    version: str | None = None
    flags: int | None = None
    details: dict[str, object] = field(default_factory=dict)

    @property
    def end_offset(self) -> int:
        return self.offset + self.size

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "kind": self.kind,
            "offset": self.offset,
            "size": self.size,
        }
        if self.version is not None:
            result["version"] = self.version
        if self.flags is not None:
            result["flags"] = self.flags
        result.update(self.details)
        return result


def _synchsafe(value: bytes) -> int | None:
    if len(value) != 4 or any(byte & 0x80 for byte in value):
        return None
    return (value[0] << 21) | (value[1] << 14) | (value[2] << 7) | value[3]


def id3v2_at(data: bytes, offset: int) -> TagInfo | None:
    if offset < 0 or offset + 10 > len(data) or data[offset : offset + 3] != b"ID3":
        return None
    major = data[offset + 3]
    revision = data[offset + 4]
    if major not in (2, 3, 4):
        return None
    flags = data[offset + 5]
    payload_size = _synchsafe(data[offset + 6 : offset + 10])
    if payload_size is None:
        return None
    footer_size = 10 if major == 4 and flags & 0x10 else 0
    total_size = 10 + payload_size + footer_size
    details: dict[str, object] = {
        "revision": revision,
        "footer": bool(footer_size),
        "unsynchronisation": bool(flags & 0x80),
    }
    return TagInfo(
        kind="ID3v2",
        offset=offset,
        size=total_size,
        version=f"2.{major}.{revision}",
        flags=flags,
        details=details,
    )


def leading_tags(data: bytes) -> list[TagInfo]:
    tags: list[TagInfo] = []
    offset = 0
    while True:
        tag = id3v2_at(data, offset)
        if tag is None or tag.size < 10 or tag.end_offset > len(data):
            break
        tags.append(tag)
        offset = tag.end_offset
    return tags


def trailing_tags(data: bytes, audio_start: int) -> list[TagInfo]:
    """Find tags commonly appended after the last MPEG frame.

    Detection walks backwards so ID3v1 and APEv2 can coexist at the file end.
    False positives are limited by requiring the documented marker and a
    plausible tag size inside the audio region.
    """

    tags: list[TagInfo] = []
    end = len(data)

    if end - 128 >= audio_start and data[end - 128 : end - 125] == b"TAG":
        tags.append(TagInfo("ID3v1", end - 128, 128))
        end -= 128

    if end - 32 >= audio_start and data[end - 32 : end - 24] == b"APETAGEX":
        footer = data[end - 32 : end]
        size = int.from_bytes(footer[12:16], "little")
        includes_header = bool(int.from_bytes(footer[20:24], "little") & 0x80000000)
        total = size + (32 if includes_header else 0)
        start = end - total
        if 0 <= start < end and size >= 32:
            tags.append(
                TagInfo(
                    "APEv2",
                    start,
                    total,
                    details={"has_header": includes_header},
                )
            )
            end = start

    if end - 10 >= audio_start and data[end - 10 : end - 7] == b"3DI":
        footer = data[end - 10 : end]
        payload_size = _synchsafe(footer[6:10])
        if payload_size is not None:
            total = 20 + payload_size
            start = end - total
            if 0 <= start < end:
                tags.append(TagInfo("ID3v2-footer", start, total, details={"footer": True}))
                end = start

    tags.sort(key=lambda tag: tag.offset)
    return tags
