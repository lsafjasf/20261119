"""Data model for parsed FLAC structure."""

from dataclasses import dataclass, field


@dataclass(slots=True)
class StreamInfo:
    min_block_size: int
    max_block_size: int
    min_frame_size: int
    max_frame_size: int
    sample_rate: int
    channels: int
    bits_per_sample: int
    total_samples: int
    md5: bytes

    @property
    def md5_hex(self) -> str:
        return self.md5.hex()

    @property
    def has_md5(self) -> bool:
        return any(self.md5)


@dataclass(slots=True)
class MetadataBlock:
    offset: int
    block_type: int
    length: int
    is_last: bool
    name: str


@dataclass(slots=True)
class SubframeInfo:
    channel: int
    kind: str
    detail: str
    declared_bits: int
    effective_bits: int
    wasted_bits: int
    samples: list[int] = field(repr=False)


@dataclass(slots=True)
class SyncRejection:
    offset: int
    reason: str


@dataclass(slots=True)
class Frame:
    offset: int
    end: int | None = None
    header_size: int | None = None
    header_crc_expected: int | None = None
    header_crc_calculated: int | None = None
    header_crc_ok: bool | None = None
    frame_crc_expected: int | None = None
    frame_crc_calculated: int | None = None
    frame_crc_ok: bool | None = None
    blocking_strategy: str | None = None
    header_number: int | None = None
    sample_number: int | None = None
    block_size: int | None = None
    sample_rate: int | None = None
    channel_assignment_code: int | None = None
    channel_assignment: str | None = None
    channels: int | None = None
    bits_per_sample: int | None = None
    subframes: list[SubframeInfo] = field(default_factory=list, repr=False)
    decoded_channels: list[list[int]] | None = field(default=None, repr=False)
    sequence_status: str = "not checked"
    errors: list[str] = field(default_factory=list)

    @property
    def size(self) -> int | None:
        return self.end - self.offset if self.end is not None else None

    @property
    def subframes_ok(self) -> bool:
        return self.decoded_channels is not None and not any(
            error.startswith("subframe") for error in self.errors
        )

    @property
    def padding_ok(self) -> bool:
        return not any(error.startswith("frame padding") for error in self.errors)

    @property
    def structurally_valid(self) -> bool:
        return (
            self.header_crc_ok is True
            and self.frame_crc_ok is True
            and self.subframes_ok
            and self.padding_ok
            and self.end is not None
            and not self.errors
        )

    @property
    def valid_audio(self) -> bool:
        return self.structurally_valid


@dataclass(slots=True)
class Recovery:
    damaged_offset: int
    resume_offset: int
    rejected: list[SyncRejection] = field(default_factory=list)


@dataclass(slots=True)
class Inspection:
    path: str | None
    data_size: int
    stream_info: StreamInfo | None
    metadata_blocks: list[MetadataBlock]
    audio_offset: int | None
    frames: list[Frame]
    recoveries: list[Recovery]
    decoded_md5: str | None
    md5_complete: bool
    errors: list[str]

    @property
    def invalid_frames(self) -> list[Frame]:
        return [frame for frame in self.frames if not frame.structurally_valid]

    @property
    def valid_frames(self) -> list[Frame]:
        return [frame for frame in self.frames if frame.structurally_valid]

    @property
    def md5_matches(self) -> bool | None:
        if not self.stream_info or not self.stream_info.has_md5:
            return None
        if not self.md5_complete or self.decoded_md5 is None:
            return None
        return self.decoded_md5 == self.stream_info.md5_hex
