"""生成演示用 BWF 文件并跑一遍核对/编辑流程。

输出到 examples/：
- odd_chunk_demo.wav   含奇数长度未知 chunk
- no_ixml_demo.wav     缺失 iXML chunk
- conflicting_time.wav bext 与 iXML 时间参考互相矛盾
- fixed_take.wav       仅修改场记（iXML.TAKE）后的导出文件
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bwftool.builder import (
    bext_chunk,
    build_wave,
    data_chunk,
    fmt_chunk,
    ixml_chunk,
    unknown_chunk,
)
from bwftool.chunks import parse_wave
from bwftool.editor import apply_edits
from bwftool.inspect import inspect_wave
from bwftool.report import render_html, render_text

EXAMPLES = ROOT / "examples"
SAMPLES = bytes((i * 17 + 3) % 256 for i in range(48))


def _write(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)


def main() -> None:
    EXAMPLES.mkdir(exist_ok=True)

    odd_chunk_wave = build_wave(
        [
            fmt_chunk(),
            bext_chunk(description="SCENE=SC01 TAKE=3"),
            ixml_chunk(),
            unknown_chunk(b"junk", b"A" * 11),  # 奇数长度
            data_chunk(SAMPLES),
        ]
    )
    no_ixml_wave = build_wave(
        [
            fmt_chunk(),
            bext_chunk(),
            unknown_chunk(b"levl", b"L" * 6),
            data_chunk(SAMPLES),
        ]
    )
    conflicting_wave = build_wave(
        [
            fmt_chunk(),
            bext_chunk(
                time_reference=48000,
                date="2026-11-19",
                time_of_day="10:00:00",
                description="SCENE=SC01 TAKE=3",
            ),
            ixml_chunk(
                offset_samples=123456,
                take_timestamp="2026-11-19T11:30:00Z",
            ),
            unknown_chunk(b"junk", b"M" * 5),
            data_chunk(SAMPLES),
        ]
    )

    _write(EXAMPLES / "odd_chunk_demo.wav", odd_chunk_wave)
    _write(EXAMPLES / "no_ixml_demo.wav", no_ixml_wave)
    _write(EXAMPLES / "conflicting_time.wav", conflicting_wave)

    fixed = apply_edits(conflicting_wave, {"iXML.TAKE": "4"})
    fixed_path = EXAMPLES / "fixed_take.wav"
    _write(fixed_path, fixed)

    before_data = parse_wave(conflicting_wave).data_chunk.data
    after_data = parse_wave(fixed).data_chunk.data
    assert before_data == after_data, "data chunk 必须逐字节不变"
    before_junk = parse_wave(conflicting_wave).chunk(b"junk")
    after_junk = parse_wave(fixed).chunk(b"junk")
    assert before_junk.data == after_junk.data, "未知 chunk 必须保留"

    result = inspect_wave(conflicting_wave)
    (EXAMPLES / "conflicting_report.txt").write_text(
        render_text(result, "conflicting_time.wav"), encoding="utf-8"
    )
    (EXAMPLES / "conflicting_report.html").write_text(
        render_html(result, "conflicting_time.wav"), encoding="utf-8"
    )

    print("演示文件已生成到 examples/")
    for path in sorted(EXAMPLES.iterdir()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        print(f"  {path.name:28s} {path.stat().st_size:6d} B  sha256:{digest}")
    print()
    print("修改场记后 data chunk 字节完全不变:", before_data == after_data)
    print("未知 junk chunk 原样保留:", before_junk.data == after_junk.data)
    print()
    print("矛盾文件的核对问题:")
    for finding in result.findings:
        print(f"  [{finding.severity}] {finding.code}: {finding.message}")


if __name__ == "__main__":
    main()
