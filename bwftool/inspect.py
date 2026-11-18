"""字段核对引擎：结构、时间参考、采样类型、来源号、场记。

每条 Finding 携带一个或多个 Locator（chunk、字段、文件字节偏移与长度），
报告层据此渲染可点击定位。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .bext import BextData
from .chunks import FmtChunk, WaveFile, parse_wave
from .ixml import IXMLData


@dataclass
class Locator:
    chunk_id: str
    field_name: str
    offset: int
    length: int

    def spec(self) -> str:
        """形如 ``bext/TimeReference@0x152(8)`` 的紧凑定位串。"""
        return (
            f"{self.chunk_id}/{self.field_name}"
            f"@0x{self.offset:X}({self.length})"
        )


@dataclass
class Finding:
    severity: str  # error | warning | info
    code: str
    message: str
    locators: list[Locator] = field(default_factory=list)


def _bext_locator(bext: BextData, name: str) -> Locator:
    offset, length = bext.field_locator(name)
    return Locator("bext", name, offset, length)


def _ixml_locator(ixml: IXMLData, name: str) -> Locator:
    offset, length = ixml.tag_locator(name)
    return Locator("iXML", name, offset, length)


def _parse_description_tokens(description: str) -> dict[str, str]:
    """从 bext.Description 提取 ``KEY=VALUE`` 或 ``KEY: VALUE`` 风格标记。"""
    tokens: dict[str, str] = {}
    for match in re.finditer(
        r"\b(SCENE|TAKE|NOTE|TAPE)\s*[=:]\s*(\S+)",
        description,
        flags=re.IGNORECASE,
    ):
        tokens[match.group(1).upper()] = match.group(2)
    return tokens


def _parse_ixml_timestamp(value: str) -> datetime | None:
    """解析 iXML TAKE_TIMESTAMP（规范允许带或不带时区）。"""
    text = value.strip()
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _structural_checks(wave: WaveFile) -> list[Finding]:
    findings: list[Finding] = []
    if wave.riff_declared_size != wave.riff_actual_size:
        findings.append(
            Finding(
                "warning",
                "RIFF_SIZE_MISMATCH",
                "RIFF 头声明大小 "
                f"{wave.riff_declared_size} 与实际 "
                f"{wave.riff_actual_size} 不一致",
                [Locator("RIFF", "size", 4, 4)],
            )
        )
    for chunk in wave.chunks:
        if chunk.is_odd:
            findings.append(
                Finding(
                    "info",
                    "ODD_CHUNK_PADDING",
                    f"chunk {chunk.chunk_id.decode('ascii', 'replace')} "
                    f"数据长度 {chunk.size} 为奇数，存在 1 字节补位",
                    [
                        Locator(
                            chunk.chunk_id.decode("ascii", "replace"),
                            "pad",
                            chunk.data_offset + chunk.size,
                            1,
                        )
                    ],
                )
            )
    if not wave.has(b"fmt "):
        findings.append(Finding("error", "NO_FMT", "缺少 fmt chunk"))
    if not wave.has(b"data"):
        findings.append(Finding("error", "NO_DATA", "缺少 data chunk"))
    if not wave.has(b"bext"):
        findings.append(
            Finding("error", "NO_BEXT", "缺少 bext chunk，不是完整 BWF 文件")
        )
    if not wave.has(b"iXML"):
        findings.append(
            Finding(
                "warning",
                "NO_IXML",
                "缺少 iXML chunk，iXML 侧字段无法核对",
            )
        )
    return findings


def _sample_type_checks(
    fmt_chunk: FmtChunk, ixml: IXMLData | None
) -> list[Finding]:
    findings: list[Finding] = []
    if ixml is None:
        return findings
    if (
        ixml.sample_rate is not None
        and int(ixml.sample_rate) != fmt_chunk.sample_rate
    ):
        findings.append(
            Finding(
                "error",
                "SAMPLE_RATE_MISMATCH",
                f"fmt 采样率 {fmt_chunk.sample_rate} Hz 与 iXML "
                f"SAMPLE_RATE {ixml.sample_rate:g} Hz 不一致",
                [_ixml_locator(ixml, "SAMPLE_RATE")],
            )
        )
    if ixml.bit_depth is not None:
        effective_bits = (
            fmt_chunk.valid_bits_per_sample
            if fmt_chunk.valid_bits_per_sample
            else fmt_chunk.bits_per_sample
        )
        if ixml.bit_depth != effective_bits:
            findings.append(
                Finding(
                    "error",
                    "BIT_DEPTH_MISMATCH",
                    f"fmt 有效位深 {effective_bits} 与 iXML BIT_DEPTH "
                    f"{ixml.bit_depth} 不一致",
                    [_ixml_locator(ixml, "BIT_DEPTH")],
                )
            )
    if ixml.file_sample_width is not None:
        if ixml.file_sample_width != fmt_chunk.bits_per_sample:
            findings.append(
                Finding(
                    "error",
                    "SAMPLE_WIDTH_MISMATCH",
                    f"fmt 容器位宽 {fmt_chunk.bits_per_sample} 与 iXML "
                    f"FILE_SAMPLE_WIDTH {ixml.file_sample_width} 不一致",
                    [_ixml_locator(ixml, "FILE_SAMPLE_WIDTH")],
                )
            )
    if ixml.file_channel_count is not None:
        if ixml.file_channel_count != fmt_chunk.channels:
            findings.append(
                Finding(
                    "error",
                    "CHANNEL_COUNT_MISMATCH",
                    f"fmt 声道数 {fmt_chunk.channels} 与 iXML "
                    f"FILE_CHANNEL_COUNT {ixml.file_channel_count} 不一致",
                    [_ixml_locator(ixml, "FILE_CHANNEL_COUNT")],
                )
            )
    if ixml.track_count is not None and ixml.track_count != fmt_chunk.channels:
        findings.append(
            Finding(
                "warning",
                "TRACK_COUNT_MISMATCH",
                f"fmt 声道数 {fmt_chunk.channels} 与 iXML TRACK_COUNT "
                f"{ixml.track_count} 不一致",
                [_ixml_locator(ixml, "TRACK_COUNT")],
            )
        )
    return findings


def _time_reference_checks(
    wave: WaveFile,
    fmt_chunk: FmtChunk,
    bext: BextData,
    ixml: IXMLData | None,
) -> list[Finding]:
    findings: list[Finding] = []
    bext_locator = _bext_locator(bext, "TimeReference")
    if ixml is None:
        return findings

    if ixml.bwf_start_time_offset_samples is not None:
        if ixml.bwf_start_time_offset_samples != bext.time_reference:
            findings.append(
                Finding(
                    "error",
                    "TIME_REFERENCE_MISMATCH",
                    "bext.TimeReference "
                    f"{bext.time_reference} 与 iXML "
                    "BWF_START_TIME_OFFSET_SAMPLES "
                    f"{ixml.bwf_start_time_offset_samples} 不一致",
                    [
                        bext_locator,
                        _ixml_locator(
                            ixml, "BWF_START_TIME_OFFSET_SAMPLES"
                        ),
                    ],
                )
            )

    if ixml.take_timestamp:
        ixml_time = _parse_ixml_timestamp(ixml.take_timestamp)
        try:
            bext_wall = datetime.strptime(
                bext.origination_date + " " + bext.origination_time,
                "%Y-%m-%d %H:%M:%S",
            )
        except ValueError:
            bext_wall = None
        if ixml_time is None:
            findings.append(
                Finding(
                    "warning",
                    "IXML_TIMESTAMP_UNPARSEABLE",
                    f"iXML TAKE_TIMESTAMP 无法解析: {ixml.take_timestamp!r}",
                    [_ixml_locator(ixml, "TAKE_TIMESTAMP")],
                )
            )
        elif bext_wall is not None:
            bext_start = bext_wall + timedelta(
                seconds=bext.time_reference / fmt_chunk.sample_rate
            )
            # bext 挂钟时间无时区；按挂钟字面时间比较，剥离 iXML 时区。
            ixml_naive = (
                ixml_time.replace(tzinfo=None)
                if ixml_time.tzinfo is not None
                else ixml_time
            )
            delta = abs((ixml_naive - bext_start).total_seconds())
            if delta > 1.0:
                findings.append(
                    Finding(
                        "error",
                        "WALLCLOCK_MISMATCH",
                        "由 bext 推算的起始挂钟时间 "
                        f"{bext_start.isoformat()} 与 iXML TAKE_TIMESTAMP "
                        f"{ixml_naive.isoformat()} 相差 {delta:.3f}s",
                        [
                            _bext_locator(bext, "OriginationDate"),
                            _bext_locator(bext, "OriginationTime"),
                            bext_locator,
                            _ixml_locator(ixml, "TAKE_TIMESTAMP"),
                        ],
                    )
                )
    return findings


def _originator_checks(bext: BextData, ixml: IXMLData | None) -> list[Finding]:
    findings: list[Finding] = []
    if not bext.originator_reference:
        findings.append(
            Finding(
                "warning",
                "EMPTY_ORIGINATOR_REFERENCE",
                "bext.OriginatorReference 为空，无法唯一标识来源录音",
                [_bext_locator(bext, "OriginatorReference")],
            )
        )
    if ixml is not None and ixml.tape and bext.originator_reference:
        if (
            ixml.tape not in bext.originator_reference
            and bext.originator_reference not in ixml.tape
        ):
            findings.append(
                Finding(
                    "warning",
                    "ORIGINATOR_TAPE_MISMATCH",
                    "bext.OriginatorReference "
                    f"{bext.originator_reference!r} 与 iXML TAPE "
                    f"{ixml.tape!r} 互不包含，来源号可能不一致",
                    [
                        _bext_locator(bext, "OriginatorReference"),
                        _ixml_locator(ixml, "TAPE"),
                    ],
                )
            )
    return findings


def _scene_take_checks(bext: BextData, ixml: IXMLData | None) -> list[Finding]:
    findings: list[Finding] = []
    tokens = _parse_description_tokens(bext.description)
    if ixml is None:
        return findings
    pairs = (
        ("SCENE", ixml.scene, "场记场景号"),
        ("TAKE", ixml.take, "场记条次号"),
        ("NOTE", ixml.note, "场记备注"),
        ("TAPE", ixml.tape, "磁带/来源号"),
    )
    for token_name, ixml_value, label in pairs:
        if token_name not in tokens or ixml_value is None:
            continue
        if tokens[token_name] != ixml_value:
            findings.append(
                Finding(
                    "error",
                    f"{token_name}_MISMATCH",
                    f"{label}不一致：bext.Description 中 {token_name}="
                    f"{tokens[token_name]!r}，iXML <{token_name}>="
                    f"{ixml_value!r}",
                    [
                        _bext_locator(bext, "Description"),
                        _ixml_locator(ixml, token_name),
                    ],
                )
            )
    return findings


@dataclass
class InspectionResult:
    wave: WaveFile
    fmt: FmtChunk | None
    bext: BextData | None
    ixml: IXMLData | None
    findings: list[Finding]

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "error" for item in self.findings)


def inspect_wave(source: bytes | str) -> InspectionResult:
    """解析并执行全部核对，返回结构化结果（含定位信息）。"""
    wave = source if isinstance(source, WaveFile) else parse_wave(source)
    findings = _structural_checks(wave)

    fmt_chunk: FmtChunk | None = None
    if wave.has(b"fmt "):
        fmt_chunk = wave.decode_fmt()

    bext: BextData | None = None
    bext_chunk = wave.chunk(b"bext")
    if bext_chunk is not None:
        bext = BextData.decode(bext_chunk)

    ixml: IXMLData | None = None
    ixml_chunk = wave.chunk(b"iXML")
    if ixml_chunk is not None:
        ixml = IXMLData.decode(ixml_chunk)

    if fmt_chunk and ixml is not None:
        findings.extend(_sample_type_checks(fmt_chunk, ixml))
    if bext is not None and fmt_chunk is not None:
        findings.extend(_time_reference_checks(wave, fmt_chunk, bext, ixml))
    if bext is not None:
        findings.extend(_originator_checks(bext, ixml))
        findings.extend(_scene_take_checks(bext, ixml))

    return InspectionResult(
        wave=wave, fmt=fmt_chunk, bext=bext, ixml=ixml, findings=findings
    )
