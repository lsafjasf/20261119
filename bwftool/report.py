"""文本与 HTML 报告渲染。HTML 为无外部依赖的单文件，定位锚点可点击。"""

from __future__ import annotations

import html

from .inspect import InspectionResult, Locator

SEVERITY_LABEL = {"error": "错误", "warning": "警告", "info": "信息"}


def render_text(result: InspectionResult, file_path: str | None = None) -> str:
    """生成纯文本报告；定位串在支持 file: 链接的终端中可点击。"""
    lines: list[str] = []
    title = file_path or "<bytes>"
    lines.append(f"BWF 核对报告: {title}")
    lines.append("=" * 60)

    if result.fmt is not None:
        fmt = result.fmt
        lines.append(
            "fmt : "
            f"{fmt.sample_rate} Hz, {fmt.channels}ch, "
            f"{fmt.bits_per_sample}-bit ({fmt.describe_sample_type()})"
        )
    if result.bext is not None:
        bext = result.bext
        lines.append(
            "bext: TimeReference="
            f"{bext.time_reference}, OriginatorReference="
            f"{bext.originator_reference!r}"
        )
        lines.append(
            f"      Description={bext.description!r}, "
            f"Date={bext.origination_date} Time={bext.origination_time}"
        )
    if result.ixml is not None:
        ixml = result.ixml
        lines.append(
            "iXML: SCENE="
            f"{ixml.scene!r}, TAKE={ixml.take!r}, TAPE={ixml.tape!r}, "
            f"offset_samples={ixml.bwf_start_time_offset_samples}"
        )

    lines.append("")
    lines.append(f"发现 {len(result.findings)} 条问题:")
    for finding in result.findings:
        lines.append(
            f"[{SEVERITY_LABEL[finding.severity]}] {finding.code}: "
            f"{finding.message}"
        )
        for locator in finding.locators:
            lines.append(f"    定位 -> {locator.spec()}")
    return "\n".join(lines)


def _anchor_id(locator: Locator) -> str:
    return f"off-{locator.offset}"


def _hex_table(result: InspectionResult, highlight: set[int]) -> str:
    """渲染完整十六进制表；行带锚点，高亮字节加 span 精确标记。"""
    raw = result.wave.raw
    rows: list[str] = []
    for row_start in range(0, len(raw), 16):
        row = raw[row_start : row_start + 16]
        hex_cells = []
        ascii_cells = []
        for index, byte in enumerate(row):
            absolute = row_start + index
            glyph = html.escape(chr(byte)) if 32 <= byte < 127 else "·"
            if absolute in highlight:
                hex_cells.append(
                    f'<td class="hl" id="off-{absolute}">{byte:02X}</td>'
                )
                ascii_cells.append(f'<td class="hl">{glyph}</td>')
            else:
                hex_cells.append(f"<td>{byte:02X}</td>")
                ascii_cells.append(f"<td>{glyph}</td>")
        rows.append(
            f'<tr id="row-{row_start}"><th>{row_start:08X}</th>'
            f"{''.join(hex_cells)}{''.join(ascii_cells)}</tr>"
        )
    return "\n".join(rows)


def render_html(
    result: InspectionResult, file_path: str | None = None
) -> str:
    """生成自包含 HTML 报告；点击问题里的定位链接跳到对应字节。"""
    title = html.escape(file_path or "<bytes>")
    highlight: set[int] = set()
    for finding in result.findings:
        for locator in finding.locators:
            highlight.update(
                range(
                    locator.offset,
                    min(
                        locator.offset + locator.length,
                        len(result.wave.raw),
                    ),
                )
            )
    finding_rows: list[str] = []
    for finding in result.findings:
        links = " ".join(
            f'<a class="loc" href="#{_anchor_id(locator)}">'
            f"{html.escape(locator.spec())}</a>"
            for locator in finding.locators
        )
        finding_rows.append(
            "<tr>"
            f'<td class="sev sev-{finding.severity}">'
            f"{SEVERITY_LABEL[finding.severity]}</td>"
            f"<td>{html.escape(finding.code)}</td>"
            f"<td>{html.escape(finding.message)}</td>"
            f"<td>{links}</td>"
            "</tr>"
        )

    summary = []
    if result.fmt is not None:
        summary.append(
            "<li>fmt: "
            f"{html.escape(result.fmt.describe_sample_type())}, "
            f"{result.fmt.sample_rate} Hz</li>"
        )
    if result.bext is not None:
        bext = result.bext
        summary.append(
            "<li>bext: TimeReference="
            f"{bext.time_reference}, OriginatorReference="
            f"{html.escape(bext.originator_reference)}</li>"
        )
    if result.ixml is not None:
        ixml = result.ixml
        summary.append(
            "<li>iXML: SCENE="
            f"{html.escape(str(ixml.scene))}, TAKE="
            f"{html.escape(str(ixml.take))}, TAPE="
            f"{html.escape(str(ixml.tape))}</li>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>BWF 核对报告 - {title}</title>
<style>
body {{ font-family: sans-serif; margin: 2rem; }}
table {{ border-collapse: collapse; margin-bottom: 2rem; }}
td, th {{ border: 1px solid #ccc; padding: 2px 6px; font-family: monospace; }}
th {{ background: #f0f0f0; text-align: left; }}
tr:target {{ background: #fff3bf; }}
td.hl {{ background: #ffd8a8; }}
td.hl:target {{ background: #fa5252; color: white; }}
.sev-error {{ color: #c92a2a; font-weight: bold; }}
.sev-warning {{ color: #e8590c; }}
.sev-info {{ color: #1971c2; }}
a.loc {{ text-decoration: none; margin-right: .5rem; }}
</style>
</head>
<body>
<h1>BWF 核对报告</h1>
<p>文件: {title}</p>
<ul>{"".join(summary)}</ul>
<h2>问题与可点击定位</h2>
<table>
<tr><th>级别</th><th>代码</th><th>说明</th><th>定位</th></tr>
{''.join(finding_rows)}
</table>
<h2>十六进制视图（点击定位跳转到此表）</h2>
<table>
{_hex_table(result, highlight)}
</table>
</body>
</html>"""
