from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HeaderInfo:
    profile: int
    show_existing_frame: bool
    show_frame: bool | None
    frame_type: int | None
    error_resilient: bool | None
    frame_to_show_map_idx: int | None = None

    @property
    def displays(self) -> bool:
        return self.show_existing_frame or bool(self.show_frame)

    @property
    def frame_type_name(self) -> str:
        if self.show_existing_frame:
            return "show_existing"
        return "key" if self.frame_type == 0 else "inter"


@dataclass
class SubframeInfo:
    index: int
    offset: int
    size: int
    header: HeaderInfo | None
    indexed: bool = True
    salvage_reason: str | None = None

    @property
    def displays(self) -> bool:
        return bool(self.header and self.header.displays)


@dataclass
class PacketReport:
    sequence: int
    timestamp: int
    packet_size: int
    is_superframe: bool
    subframes: list[SubframeInfo] = field(default_factory=list)
    marker: int | None = None
    index_offset: int | None = None
    index_size: int = 0
    declared_frame_count: int = 1
    sizes: list[int] = field(default_factory=list)
    size_sum: int = 0
    payload_size: int = 0
    corrupted: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def visible_count(self) -> int:
        return sum(subframe.displays for subframe in self.subframes)

    @property
    def last_presented_subframe_index(self) -> int | None:
        for subframe in reversed(self.subframes):
            if subframe.displays:
                return subframe.index
        return None
