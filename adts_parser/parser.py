"""Stream-level ADTS parsing: validation, segmentation and truncation handling."""

import json
from dataclasses import asdict, dataclass, field

from .adts import ADTSHeader, ADTSParseError, parse_adts_header
from .id3 import ID3Tag, looks_like_id3v2, parse_id3v2_header


@dataclass
class TruncatedFrame:
    """A trailing (or resync-skipped) region that is not a playable frame."""

    offset: int           # absolute file offset where the region starts
    available: int        # bytes available from ``offset`` to end of stream
    expected_length: int  # frame_length advertised by the header, or 0
    reason: str


@dataclass
class Segment:
    """One logical track found inside a concatenated stream.

    ``start`` points at the first byte belonging to the segment (an ID3 tag
    when present, otherwise the first ADTS header).  ``end`` is one past the
    last byte owned by the segment, while ``playable_end`` excludes any
    trailing truncated frame so duration/sample counts stay playback-accurate.
    """

    index: int
    start: int
    end: int
    playable_end: int
    id3: ID3Tag | None = None
    frames: list[ADTSHeader] = field(default_factory=list)
    sample_rate: int = 0
    sample_rate_index: int = -1
    profile: int = -1
    audio_object_type: int = 0
    channel_config: int = 0
    sample_count: int = 0
    truncated: TruncatedFrame | None = None

    @property
    def duration_seconds(self) -> float:
        """Playable duration; truncated tail frames are never counted."""
        if self.sample_rate == 0:
            return 0.0
        return self.sample_count / self.sample_rate

    @property
    def frame_count(self) -> int:
        return len(self.frames)


@dataclass
class SegmentIndex:
    """Exportable, byte-accurate index of every segment in a stream."""

    source: str
    file_size: int
    segments: list[Segment]
    truncated: list[TruncatedFrame]

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "file_size": self.file_size,
            "segment_count": len(self.segments),
            "segments": [
                {
                    "index": seg.index,
                    "start": seg.start,
                    "end": seg.end,
                    "playable_end": seg.playable_end,
                    "id3": None
                    if seg.id3 is None
                    else {"offset": seg.id3.offset, "size": seg.id3.total_size},
                    "frame_count": seg.frame_count,
                    "sample_rate": seg.sample_rate,
                    "sample_rate_index": seg.sample_rate_index,
                    "profile": seg.profile,
                    "audio_object_type": seg.audio_object_type,
                    "channel_config": seg.channel_config,
                    "sample_count": seg.sample_count,
                    "duration_seconds": round(seg.duration_seconds, 6),
                    "truncated": None if seg.truncated is None else asdict(seg.truncated),
                }
                for seg in self.segments
            ],
            "truncated_regions": [asdict(item) for item in self.truncated],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def extract_segment_bytes(self, data: bytes, segment_index: int) -> bytes:
        """Re-read exactly one segment's bytes without touching neighbours.

        The slice spans the ID3 tag (if any) through the last complete frame,
        so a standalone decoder can consume it and adjacent ID3 data stays
        attached to its own segment.
        """

        seg = self.segments[segment_index]
        return bytes(data[seg.start : seg.end])


def _find_next_sync(data: bytes, start: int) -> int:
    """Locate the next 0xFFF ADTS sync word at or after ``start``."""

    pos = data.find(b"\xff", start)
    while pos != -1 and pos + 1 < len(data):
        if data[pos + 1] & 0xF0 == 0xF0:
            return pos
        pos = data.find(b"\xff", pos + 1)
    return -1


def parse_stream(
    data: bytes | bytearray | memoryview,
    source: str = "<memory>",
    *,
    split_on_param_change: bool = True,
) -> SegmentIndex:
    """Parse a (possibly multi-track concatenated) ADTS/ID3 byte stream.

    Segmentation rules:
      * an ID3v2 tag always opens a new segment and is fully included in it;
      * with ``split_on_param_change``, a change of sample rate/profile/
        channel layout between adjacent ADTS frames also opens a segment;
      * a frame whose declared length runs past EOF is reported as truncated,
        excluded from duration/sample totals and never counted as playable.
    """

    data = bytes(data)
    segments: list[Segment] = []
    truncated_regions: list[TruncatedFrame] = []

    current: Segment | None = None
    pos = 0
    size = len(data)

    def open_segment(start: int, tag: ID3Tag | None) -> Segment:
        seg = Segment(index=len(segments), start=start, end=start, playable_end=start, id3=tag)
        segments.append(seg)
        return seg

    while pos < size:
        if looks_like_id3v2(data, pos):
            try:
                tag = parse_id3v2_header(data, pos)
            except ValueError as exc:
                raise ADTSParseError(str(exc)) from exc
            # An ID3 tag belongs wholly to the following track; close the
            # previous segment right at the tag start.
            if current is not None:
                current.end = pos
                current.playable_end = pos
            current = open_segment(pos, tag)
            pos += tag.total_size
            continue

        sync_pos = _find_next_sync(data, pos)
        if sync_pos == -1:
            # Trailing non-ADTS garbage: report once and stop scanning.
            truncated_regions.append(
                TruncatedFrame(
                    offset=pos,
                    available=size - pos,
                    expected_length=0,
                    reason="no ADTS sync word found in remaining bytes",
                )
            )
            break
        if sync_pos > pos:
            truncated_regions.append(
                TruncatedFrame(
                    offset=pos,
                    available=sync_pos - pos,
                    expected_length=0,
                    reason=f"skipped {sync_pos - pos} non-ADTS byte(s) while resynchronizing",
                )
            )
            pos = sync_pos

        try:
            header = parse_adts_header(data, pos)
        except ADTSParseError as exc:
            truncated_regions.append(
                TruncatedFrame(
                    offset=pos,
                    available=size - pos,
                    expected_length=0,
                    reason=str(exc),
                )
            )
            pos += 1
            continue

        frame_end = pos + header.frame_length
        if frame_end > size:
            # Truncated tail frame: record the exact offset and stop the
            # playable region before it.
            trunc = TruncatedFrame(
                offset=pos,
                available=size - pos,
                expected_length=header.frame_length,
                reason=(
                    f"frame declares {header.frame_length} bytes but only "
                    f"{size - pos} remain; missing {frame_end - size} byte(s)"
                ),
            )
            truncated_regions.append(trunc)
            if current is None:
                current = open_segment(pos, None)
            current.truncated = trunc
            current.end = size
            break

        if current is None:
            current = open_segment(pos, None)

        params = (header.sample_rate, header.profile, header.channel_config)
        current_params = (current.sample_rate, current.profile, current.channel_config)
        if (
            split_on_param_change
            and current.frame_count > 0
            and params != current_params
        ):
            current.end = pos
            current.playable_end = pos
            current = open_segment(pos, None)

        if current.frame_count == 0:
            current.sample_rate = header.sample_rate
            current.sample_rate_index = header.sample_rate_index
            current.profile = header.profile
            current.audio_object_type = header.audio_object_type
            current.channel_config = header.channel_config

        current.frames.append(header)
        current.sample_count += header.sample_count
        current.playable_end = frame_end
        current.end = frame_end
        pos = frame_end

    for seg in segments:
        # A segment that only contains an ID3 tag (no frames) must still
        # report its full byte span so exported offsets stay lossless.
        seg.end = max(seg.end, seg.playable_end, seg.start + (seg.id3.total_size if seg.id3 else 0))
        seg.playable_end = max(seg.playable_end, seg.start)

    return SegmentIndex(
        source=source,
        file_size=size,
        segments=segments,
        truncated=truncated_regions,
    )
