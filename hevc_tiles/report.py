"""Self-contained HTML/JSON report rendering."""

from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path

from .analysis import Analysis
from .decode import Verification
from .image import compose_masked, write_png, yuv420_to_rgb
from .layout import slice_tile_ranges
from .models import StreamInfo


def generate_report(
    info: StreamInfo,
    analysis: Analysis,
    available: set[int],
    output_dir: str | Path,
    verification: Verification | None = None,
) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if verification is not None and verification.partial_frame is not None:
        width = verification.full_frame.width
        height = verification.full_frame.height
        write_png(
            output / "full_decode.png",
            yuv420_to_rgb(verification.full_frame),
            width,
            height,
        )
        write_png(
            output / "partial_masked.png",
            compose_masked(
                verification.partial_frame, info.layout.tiles, available
            ),
            width,
            height,
        )
    if verification is not None and verification.partial_stream:
        (output / "partial_access_unit.hevc").write_bytes(
            verification.partial_stream
        )

    status, status_title, status_detail = _status(
        info, analysis, available, verification
    )
    payload = _json_payload(info, analysis, available, verification, status)
    (output / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    page = _render_page(
        info,
        analysis,
        available,
        verification,
        status,
        status_title,
        status_detail,
    )
    report_path = output / "index.html"
    report_path.write_text(page, encoding="utf-8")
    return report_path


def _status(
    info: StreamInfo,
    analysis: Analysis,
    available: set[int],
    verification: Verification | None,
) -> tuple[str, str, str]:
    all_tiles = {tile.id for tile in info.layout.tiles} if info.layout else set()
    missing = all_tiles - available
    if not available:
        return "blocked", "未选择可用瓦片", "没有可解码和核验的区域。"
    if not analysis.local_allowed:
        return (
            "blocked",
            "未执行局部展示",
            "该配置不满足当前工具要求的独立解码约束。",
        )
    if verification is None:
        return (
            "blocked",
            "未执行像素核验",
            "独立提取条件已满足，但没有运行完整/局部解码，因此不生成局部画面。",
        )
    if verification.error:
        return "failed", "局部解码核验未完成", verification.error
    if not verification.exact:
        mismatches = sum(
            item.mismatches for item in verification.comparisons.values()
        )
        return (
            "failed",
            "局部解码核验失败",
            f"可用瓦片内有 {mismatches} 个采样值与完整解码不同，不能视为成功。",
        )
    if missing:
        return (
            "partial",
            "局部瓦片核验通过",
            f"{len(available)}/{len(all_tiles)} 个瓦片逐像素一致；"
            f"{len(missing)} 个缺失瓦片已掩码，整帧并未完整解码。",
        )
    return (
        "verified",
        "全部瓦片逐像素核验通过",
        "所有瓦片均可用，局部码流与完整解码逐像素一致。",
    )


def _json_payload(
    info: StreamInfo,
    analysis: Analysis,
    available: set[int],
    verification: Verification | None,
    status: str,
) -> dict:
    per_frame_counts = (
        slice_tile_ranges(info.layout, analysis.frame_slices)
        if info.layout
        else {}
    )
    result = info.to_basic_dict()
    result.update(
        {
            "status": status,
            "frame_index": analysis.frame_index,
            "local_decode_allowed": analysis.local_allowed,
            "blocked_reasons": analysis.blocked_reasons,
            "available_tiles": sorted(available),
            "missing_tiles": (
                sorted({tile.id for tile in info.layout.tiles} - available)
                if info.layout
                else []
            ),
            "conditions": [
                {
                    "key": item.key,
                    "label": item.label,
                    "passed": item.passed,
                    "detail": item.detail,
                }
                for item in analysis.conditions
            ],
            "dependencies": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "kind": edge.kind,
                    "detail": edge.detail,
                }
                for edge in analysis.dependencies
            ],
            "tiles": [
                {
                    "id": tile.id,
                    "x": tile.x,
                    "y": tile.y,
                    "width": tile.width,
                    "height": tile.height,
                    "ctb_x": tile.ctb_x,
                    "ctb_y": tile.ctb_y,
                    "ctb_width": tile.ctb_width,
                    "ctb_height": tile.ctb_height,
                    "ts_start": tile.ts_start,
                    "ts_end": tile.ts_end,
                    "available": tile.id in available,
                    "slice_count_frame": per_frame_counts.get(tile.id, 0),
                }
                for tile in info.layout.tiles
            ],
            "metrics": [
                {
                    "tile_id": metric.tile_id,
                    "entry_offsets": metric.entry_offsets,
                    "absolute_offsets": metric.absolute_offsets,
                    "substream_bytes": metric.substream_bytes,
                    "slice_count_all_frames": metric.slice_count,
                    "samples": metric.samples,
                    "coded_bytes": metric.coded_bytes,
                    "average_bytes": metric.average_bytes,
                    "average_bits": metric.average_bits,
                }
                for metric in analysis.metrics
            ],
            "verification": (
                {
                    "decoder": verification.decoder,
                    "exact": verification.exact,
                    "error": verification.error,
                    "tiles": {
                        str(key): {
                            "samples": value.samples,
                            "mismatches": value.mismatches,
                            "max_error": value.max_error,
                            "mean_error": value.mean_error,
                        }
                        for key, value in verification.comparisons.items()
                    },
                }
                if verification
                else None
            ),
        }
    )
    return result


def _render_page(
    info: StreamInfo,
    analysis: Analysis,
    available: set[int],
    verification: Verification | None,
    status: str,
    status_title: str,
    status_detail: str,
) -> str:
    assert info.sps is not None and info.pps is not None and info.layout is not None
    sps, pps, layout = info.sps, info.pps, info.layout
    all_tiles = {tile.id for tile in layout.tiles}
    missing = sorted(all_tiles - available)
    frame_counts = slice_tile_ranges(layout, analysis.frame_slices)
    verification_rows = []
    for tile in layout.tiles:
        metric = next(item for item in analysis.metrics if item.tile_id == tile.id)
        comparison = (
            verification.comparisons.get(tile.id) if verification else None
        )
        verification_rows.append(
            _tile_row(
                tile,
                metric,
                frame_counts.get(tile.id, 0),
                tile.id in available,
                comparison,
                verification is not None,
            )
        )
    verification_header = (
        "<th>采样数</th><th>不一致</th><th>最大误差</th><th>结论</th>"
        if verification and verification.comparisons
        else ""
    )
    status_class = {
        "verified": "ok",
        "partial": "partial",
        "failed": "bad",
        "blocked": "blocked",
    }[status]
    warnings = "".join(
        f"<li>{html.escape(item)}</li>" for item in info.warnings
    ) or "<li>无解析警告</li>"
    payload_json = json.dumps(
        _json_payload(info, analysis, available, verification, status),
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    partial_footer = (
        " · 局部码流: <code>partial_access_unit.hevc</code>"
        if verification is not None and verification.partial_stream
        else ""
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HEVC 瓦片布局与局部解码核验</title>
<style>{CSS}</style>
</head>
<body>
<main>
  <header class="hero">
    <div>
      <p class="eyebrow">HEVC / TILE FORENSICS</p>
      <h1>瓦片布局、码量与解码边界</h1>
      <p class="file">{html.escape(str(info.path))}</p>
    </div>
    <div class="status {status_class}">
      <span class="dot"></span>
      <div><strong>{html.escape(status_title)}</strong>
      <p>{html.escape(status_detail)}</p></div>
    </div>
  </header>

  <section class="notice {status_class}">
    <strong>边界声明</strong>
    <span>只有满足独立提取条件的可用瓦片会参与局部展示。缺失区域始终显示为掩码；
    局部核验通过不等于整帧解码成功。</span>
  </section>

  <section class="cards">
    {_card("画面", f"{sps.width} × {sps.height}")}
    {_card("CTB", f"{sps.ctb_size} px · {layout.pic_width_in_ctbs}×{layout.pic_height_in_ctbs}")}
    {_card("瓦片", f"{layout.num_columns} 列 × {layout.num_rows} 行")}
    {_card("当前帧 / 总帧", f"{analysis.frame_index} / {len({item.au_index for item in info.slices})}")}
    {_card("可用 / 缺失", f"{len(available)} / {len(missing)}")}
    {_card("跨瓦片依赖", "无" if not analysis.cross_tile_dependencies else str(len(analysis.cross_tile_dependencies)))}
  </section>

  <section class="panel">
    <div class="section-title">
      <div><p class="eyebrow">LAYOUT + BITSTREAM</p><h2>瓦片索引与入口点</h2></div>
      <p class="hint">偏移为相对 slice_segment_data 的 RBSP 字节位置；文件偏移为绝对字节位置。</p>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>瓦片</th><th>像素区域</th><th>CTB 区域</th><th>入口点偏移</th>
          <th>子流字节</th><th>切片数（本帧/总）</th><th>平均码量</th>
          <th>可用性</th>{verification_header}
        </tr></thead>
        <tbody>{''.join(verification_rows)}</tbody>
      </table>
    </div>
  </section>

  <section class="grid2">
    <div class="panel">
      <div class="section-title">
        <div><p class="eyebrow">DECODE DEPENDENCY</p><h2>瓦片依赖图</h2></div>
      </div>
      {_dependency_graph(info, analysis, available)}
      <div class="dependency-list">{_dependency_text(analysis)}</div>
    </div>
    <div class="panel">
      <div class="section-title">
        <div><p class="eyebrow">INDEPENDENCE GATE</p><h2>局部提取条件</h2></div>
        <span class="gate {'pass' if analysis.local_allowed else 'fail'}">
          {'允许局部展示' if analysis.local_allowed else '拒绝局部展示'}
        </span>
      </div>
      <div class="conditions">{_condition_rows(analysis)}</div>
    </div>
  </section>

  {_visual_section(info, analysis, available, verification, status)}

  <section class="grid2">
    <div class="panel">
      <div class="section-title"><div><p class="eyebrow">CONFIG</p><h2>码流参数</h2></div></div>
      <dl class="kv">
        <dt>profile_idc</dt><dd>{sps.profile_idc}</dd>
        <dt>chroma / bit depth</dt><dd>{sps.chroma_format_idc} / {sps.bit_depth_luma}-bit</dd>
        <dt>tiles_enabled_flag</dt><dd>{_bool(pps.tiles_enabled)}</dd>
        <dt>uniform_spacing_flag</dt><dd>{_bool(pps.uniform_spacing)}</dd>
        <dt>loop_filter_across_tiles</dt><dd>{_bool(pps.loop_filter_across_tiles_enabled)}</dd>
        <dt>dependent_slice_segments</dt><dd>{_bool(pps.dependent_slice_segments_enabled)}</dd>
        <dt>entropy_coding_sync (WPP)</dt><dd>{_bool(pps.entropy_coding_sync_enabled)}</dd>
        <dt>SAO</dt><dd>{_bool(sps.sao_enabled)}</dd>
        <dt>NALU / Slice</dt><dd>{len(info.nals)} / {len(info.slices)}</dd>
      </dl>
    </div>
    <div class="panel">
      <div class="section-title"><div><p class="eyebrow">PARSER NOTES</p><h2>解析状态</h2></div></div>
      <ul class="warnings">{warnings}</ul>
      <p class="fine">所有字段来自 Annex B RBSP。局部码流会保留 VPS/SPS/PPS，
      仅送入可用瓦片对应的切片；若缺失瓦片 0，则以首切片头部的无数据占位符维持解码地址。</p>
    </div>
  </section>

  <footer>
    <p>完整解码与局部解码均由独立 FFmpeg 进程输出 8-bit YUV 4:2:0，
    再对可用瓦片的 Y/Cb/Cr 全部采样逐点比较。</p>
    <p>JSON: <code>report.json</code>{partial_footer}</p>
  </footer>
</main>
<script id="report-data" type="application/json">{payload_json}</script>
<script>{JS}</script>
</body>
</html>"""


def _card(label: str, value: str) -> str:
    return (
        f'<article class="card"><span>{html.escape(label)}</span>'
        f"<strong>{html.escape(value)}</strong></article>"
    )


def _bool(value: bool) -> str:
    return "1 / 开启" if value else "0 / 关闭"


def _compact(values: list) -> str:
    if not values:
        return "—"
    counts = Counter(values)
    parts = []
    for value, count in counts.items():
        if value is None:
            text = "—"
        else:
            text = str(value)
        parts.append(f"{text}×{count}" if count > 1 else text)
    return ", ".join(parts)


def _tile_row(
    tile, metric, frame_count, available, comparison, with_verification
) -> str:
    comparison_cells = ""
    if comparison is not None:
        result = (
            '<span class="tag good">逐点一致</span>'
            if comparison.exact
            else '<span class="tag bad">不一致</span>'
        )
        comparison_cells = (
            f"<td>{comparison.samples}</td><td>{comparison.mismatches}</td>"
            f"<td>{comparison.max_error}</td><td>{result}</td>"
        )
    elif with_verification:
        comparison_cells = (
            '<td colspan="4"><span class="tag missing">未解码 / 仅掩码</span></td>'
        )
    availability = (
        '<span class="tag good">可用</span>'
        if available
        else '<span class="tag missing">缺失 / 掩码</span>'
    )
    offsets = _compact(metric.entry_offsets)
    absolute = _compact(metric.absolute_offsets)
    offset_text = (
        f"{offsets}<small>file: {absolute}</small>"
        if metric.entry_offsets
        else f"0 <small>file: {absolute}</small>"
    )
    return f"""<tr data-tile="{tile.id}" class="{'is-missing' if not available else ''}">
      <td><b>T{tile.id}</b></td>
      <td>{tile.x},{tile.y}<small>{tile.width}×{tile.height}</small></td>
      <td>{tile.ctb_x},{tile.ctb_y}<small>TS {tile.ts_start}–{tile.ts_end - 1}</small></td>
      <td class="mono">{offset_text}</td>
      <td class="mono">{_compact(metric.substream_bytes)}</td>
      <td>{frame_count} / {metric.slice_count}</td>
      <td>{metric.average_bytes:,.0f} B<small>{metric.average_bits:,.0f} bit</small></td>
      <td>{availability}</td>
      {comparison_cells}
    </tr>"""


def _dependency_graph(info: StreamInfo, analysis: Analysis, available: set[int]) -> str:
    assert info.layout is not None
    columns = info.layout.num_columns
    rows = info.layout.num_rows
    node_w, node_h = 156, 76
    pad_x, pad_y = 58, 54
    step_x, step_y = 210, 132
    width = pad_x * 2 + max(1, columns) * step_x
    height = pad_y * 2 + max(1, rows) * step_y
    positions = {}
    for tile in info.layout.tiles:
        positions[tile.id] = (
            pad_x + tile.column * step_x,
            pad_y + tile.row * step_y,
        )
    edges = [
        edge
        for edge in analysis.dependencies
        if edge.kind != "intra-tile" and edge.source != edge.target
    ]
    edge_svg = []
    for edge in edges:
        sx, sy = positions[edge.source]
        tx, ty = positions[edge.target]
        x1 = sx + node_w / 2
        y1 = sy + node_h / 2
        x2 = tx + node_w / 2
        y2 = ty + node_h / 2
        mx = (x1 + x2) / 2
        my = (y1 + y2) / 2 - 30
        edge_svg.append(
            f'<path class="dependency" d="M{x1:.1f},{y1:.1f} Q{mx:.1f},{my:.1f} '
            f'{x2:.1f},{y2:.1f}" marker-end="url(#arrow)"/>'
        )
    nodes = []
    for tile in info.layout.tiles:
        x, y = positions[tile.id]
        classes = "tile-node"
        if tile.id not in available:
            classes += " missing"
        nodes.append(
            f'<g class="{classes}" data-tile="{tile.id}" transform="translate({x},{y})">'
            f'<rect width="{node_w}" height="{node_h}" rx="14"/>'
            f'<text class="node-id" x="16" y="31">T{tile.id}</text>'
            f'<text class="node-meta" x="16" y="53">{tile.width}×{tile.height} · '
            f"{'可用' if tile.id in available else '缺失'}</text>"
            f"</g>"
        )
    has_internal = any(
        edge.source == edge.target and edge.kind != "intra-tile"
        for edge in analysis.dependencies
    )
    if not edges and has_internal:
        note = (
            '<p class="graph-note">无跨瓦片箭头，但瓦片内部仍有 WPP/切片/参考依赖；'
            "条件区给出阻断原因。</p>"
        )
    elif not edges:
        note = (
            '<p class="graph-note">节点之间无箭头：瓦片可独立解码，'
            "不存在跨瓦片像素/上下文依赖。</p>"
        )
    else:
        note = (
            '<p class="graph-note">箭头表示目标瓦片需要源瓦片；'
            "虚线仅为画面中缺失节点的视觉区分。</p>"
        )
    return f"""<div class="graph-wrap">
  <svg viewBox="0 0 {width} {height}" role="img" aria-label="瓦片解码依赖图">
    <defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4"
      orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="currentColor"/></marker></defs>
    <g class="edges">{''.join(edge_svg)}</g>
    {''.join(nodes)}
  </svg>
  {note}
</div>"""


def _dependency_text(analysis: Analysis) -> str:
    cross = analysis.cross_tile_dependencies
    internal = [
        edge
        for edge in analysis.dependencies
        if edge.source == edge.target and edge.kind != "intra-tile"
    ]
    if not cross:
        if internal:
            kinds = ", ".join(sorted({edge.kind for edge in internal}))
            detail = internal[0].detail
            return (
                '<div class="dep-blocked"><b>无跨瓦片箭头，但存在瓦片内部依赖。</b>'
                f" {html.escape(kinds)}：{html.escape(detail)}</div>"
            )
        return (
            '<div class="dep-ok"><b>无跨瓦片解码边。</b>'
            "每个瓦片仅依赖自身内部 CTB/切片顺序。</div>"
        )
    items = "".join(
        f"<li>T{edge.source} → T{edge.target} "
        f"<span>{html.escape(edge.kind)}</span>：{html.escape(edge.detail)}</li>"
        for edge in cross
    )
    return f"<ul class='dep-list'>{items}</ul>"


def _condition_rows(analysis: Analysis) -> str:
    rows = []
    for item in analysis.conditions:
        mark = "✓" if item.passed else "✕"
        required = "" if item.required_for_local else " advisory"
        rows.append(
            f'<div class="condition {"pass" if item.passed else "fail"}{required}">'
            f'<span class="mark">{mark}</span><div><b>{html.escape(item.label)}</b>'
            f"<p>{html.escape(item.detail)}</p></div></div>"
        )
    return "".join(rows)


def _visual_section(
    info: StreamInfo,
    analysis: Analysis,
    available: set[int],
    verification: Verification | None,
    status: str,
) -> str:
    assert info.layout is not None
    if not analysis.local_allowed:
        return f"""<section class="panel blocked-visual">
          <div class="section-title"><div><p class="eyebrow">LOCAL VIEW WITHHELD</p>
          <h2>不执行局部像素展示</h2></div>
          <span class="tag bad">依赖条件不满足</span></div>
          <p>该测试流没有同时满足“无跨瓦片滤波、无依赖切片、无 WPP、全 IDR I 切片、
          entry point 与完整瓦片边界可隔离”的独立提取条件。为避免把错误区域当作成功解码，
          本报告不生成局部帧。</p>
          <ul class="reason-list">{''.join(f'<li>{html.escape(reason)}</li>' for reason in analysis.blocked_reasons)}</ul>
        </section>"""
    if verification is None:
        return """<section class="panel blocked-visual">
          <div class="section-title"><div><p class="eyebrow">NO REFERENCE</p>
          <h2>缺少完整解码参考</h2></div></div>
          <p>当前环境未完成完整解码，因此不能证明可用瓦片像素。未生成局部视觉结果。</p>
        </section>"""
    if verification.error:
        return f"""<section class="panel blocked-visual">
          <div class="section-title"><div><p class="eyebrow">DECODE ERROR</p>
          <h2>局部解码未完成</h2></div><span class="tag bad">失败</span></div>
          <p>{html.escape(verification.error)}</p>
          <p>未生成虚假的“成功”局部画面。</p>
        </section>"""

    overlay_full = _image_overlay(info, available, "full")
    overlay_partial = _image_overlay(info, available, "partial")
    missing = len({tile.id for tile in info.layout.tiles} - available)
    partial_class = "partial" if missing else "verified"
    mask_legend = (
        '<div class="mask-legend"><span class="swatch"></span>'
        "紫色斜纹图案为缺失瓦片，不代表任何解码像素。</div>"
        if missing
        else '<div class="mask-legend ok-legend">所有瓦片均可用，没有掩码区域。</div>'
    )
    return f"""<section class="panel">
      <div class="section-title">
        <div><p class="eyebrow">FULL DECODE VS LOCAL DECODE</p>
        <h2>可用瓦片像素核验</h2></div>
        <span class="tag {'good' if verification.exact else 'bad'}">
          {'逐点一致' if verification.exact else '存在差异'}</span>
      </div>
      <div class="visual-grid">
        <figure>
          <figcaption><b>完整解码参考</b><span>整帧，仅作对照</span></figcaption>
          <div class="image-box" style="aspect-ratio:{info.sps.width}/{info.sps.height}">
            <img src="full_decode.png" alt="完整解码参考帧">
            {overlay_full}
          </div>
        </figure>
        <figure>
          <figcaption><b>局部解码 + 缺失掩码</b>
          <span>{len(available)} 个瓦片可用 · {missing} 个区域掩码</span></figcaption>
          <div class="image-box {partial_class}" style="aspect-ratio:{info.sps.width}/{info.sps.height}">
            <img src="partial_masked.png" alt="局部解码帧，缺失瓦片显示掩码">
            {overlay_partial}
          </div>
        </figure>
      </div>
      {mask_legend}
      {_comparison_table(verification)}
    </section>"""


def _image_overlay(info: StreamInfo, available: set[int], mode: str) -> str:
    assert info.layout is not None and info.sps is not None
    parts = []
    for tile in info.layout.tiles:
        if mode == "full":
            classes = "tile-box reference"
        else:
            classes = (
                "tile-box available" if tile.id in available else "tile-box missing"
            )
        parts.append(
            f'<span class="{classes}" style="left:{tile.x / info.sps.width * 100:.4f}%;'
            f"top:{tile.y / info.sps.height * 100:.4f}%;"
            f"width:{tile.width / info.sps.width * 100:.4f}%;"
            f'height:{tile.height / info.sps.height * 100:.4f}%">T{tile.id}</span>'
        )
    return "".join(parts)


def _comparison_table(verification: Verification) -> str:
    rows = "".join(
        f"<tr><td>T{tile_id}</td><td>{item.samples}</td>"
        f"<td>{item.mismatches}</td><td>{item.max_error}</td>"
        f"<td>{item.mean_error:.6f}</td>"
        f"<td>{'<span class=\"tag good\">一致</span>' if item.exact else '<span class=\"tag bad\">失败</span>'}</td></tr>"
        for tile_id, item in sorted(verification.comparisons.items())
    )
    return f"""<div class="table-wrap compact">
      <table><thead><tr><th>瓦片</th><th>采样数</th><th>不一致</th>
      <th>最大绝对误差</th><th>平均绝对误差</th><th>结论</th></tr></thead>
      <tbody>{rows}</tbody></table></div>"""


CSS = """
:root{
  --bg:#0d1117;--panel:#151b23;--panel2:#1b2430;--line:#2b3644;
  --text:#e8edf3;--muted:#8f9cad;--green:#59d99d;--green-bg:#123629;
  --amber:#f2bd62;--amber-bg:#3a2b14;--red:#ff7b85;--red-bg:#3b1d23;
  --pink:#e786ff;--cyan:#78d6ef;--mono:"SFMono-Regular",Consolas,"Liberation Mono",monospace;
}
*{box-sizing:border-box}
body{margin:0;background:
  radial-gradient(900px 500px at 85% -10%,#1b3140 0,transparent 60%),
  var(--bg);color:var(--text);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,
  "Segoe UI","Noto Sans SC",sans-serif}
main{max-width:1440px;margin:auto;padding:38px 32px 60px}
.hero{display:flex;justify-content:space-between;gap:30px;align-items:flex-start;
  padding:18px 0 30px;border-bottom:1px solid var(--line)}
h1{font-size:clamp(30px,4vw,52px);line-height:1.05;margin:5px 0 12px;letter-spacing:-.03em}
h2{font-size:21px;margin:3px 0 0;letter-spacing:-.02em}
.eyebrow{font:700 11px/1 var(--mono);letter-spacing:.15em;color:var(--cyan);
  text-transform:uppercase;margin:0}
.file{margin:0;color:var(--muted);font-family:var(--mono);font-size:12px}
.status{display:flex;gap:14px;min-width:360px;max-width:470px;padding:18px 20px;
  border:1px solid var(--line);background:var(--panel);border-radius:16px}
.status p{margin:4px 0 0;color:var(--muted);font-size:13px}
.status strong{display:block;font-size:15px}
.dot{width:11px;height:11px;border-radius:50%;margin-top:5px;flex:none;background:var(--amber)}
.status.ok{border-color:#28694d;background:var(--green-bg)}.status.ok .dot{background:var(--green)}
.status.partial{border-color:#5f4a24;background:var(--amber-bg)}.status.partial .dot{background:var(--amber)}
.status.bad{border-color:#6e3038;background:var(--red-bg)}.status.bad .dot{background:var(--red)}
.status.blocked .dot{background:var(--muted)}
.notice{display:flex;gap:14px;margin:18px 0 20px;padding:12px 16px;border-radius:12px;
  border:1px solid var(--line);background:#121820;color:var(--muted);font-size:13px}
.notice strong{color:var(--text);white-space:nowrap}
.notice.partial{border-color:#5f4a24}.notice.bad{border-color:#6e3038}
.cards{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin:18px 0}
.card{padding:14px 16px;background:var(--panel);border:1px solid var(--line);border-radius:13px}
.card span{display:block;font:700 10px/1 var(--mono);letter-spacing:.11em;color:var(--muted);
  text-transform:uppercase;margin-bottom:9px}.card strong{font-size:16px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:22px;
  margin:16px 0;overflow:hidden}
.section-title{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;
  margin-bottom:18px}.hint{max-width:480px;color:var(--muted);font-size:12px;margin:0;text-align:right}
.grid2{display:grid;grid-template-columns:1.35fr .9fr;gap:16px}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:12px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;color:var(--muted);font:700 10px/1.2 var(--mono);letter-spacing:.08em;
  text-transform:uppercase;background:#111821;padding:11px 12px;border-bottom:1px solid var(--line);
  white-space:nowrap}
td{padding:12px;border-bottom:1px solid #222d39;vertical-align:top}
tr:last-child td{border-bottom:0}
tbody tr:hover{background:#18222d}
td small{display:block;color:var(--muted);font-size:10.5px;margin-top:2px}
tr.is-missing{background:linear-gradient(90deg,#24172555,transparent 60%)}
.mono{font-family:var(--mono);font-size:11.5px}
.tag{display:inline-block;padding:4px 8px;border-radius:999px;font-size:10.5px;
  font-weight:700;border:1px solid var(--line);white-space:nowrap}
.tag.good{color:var(--green);background:var(--green-bg);border-color:#28694d}
.tag.bad{color:var(--red);background:var(--red-bg);border-color:#6e3038}
.tag.missing{color:var(--pink);background:#2d1934;border-color:#673a72}
.graph-wrap svg{width:100%;height:auto;max-height:420px;color:#7b8ba0}
.tile-node rect{fill:#1b2733;stroke:#3c4d5f;stroke-width:1.2}
.tile-node .node-id{fill:#f1f6fb;font:800 20px var(--mono)}
.tile-node .node-meta{fill:#8fa2b4;font:11px var(--mono)}
.tile-node.missing rect{fill:#2b1a31;stroke:#8e4b9e;stroke-dasharray:5 4}
.tile-node.missing .node-id{fill:var(--pink)}
.tile-node.focus rect{stroke:var(--cyan);stroke-width:3;filter:drop-shadow(0 0 8px #78d6ef66)}
.dependency{fill:none;stroke:#e7a95e;stroke-width:2;opacity:.9}
.graph-note{color:var(--muted);font-size:12px;margin:4px 0 0}
.dep-ok,.dep-list li{padding:10px 12px;background:#111821;border:1px solid var(--line);
  border-radius:10px;font-size:12px}.dep-ok b{color:var(--green)}
.dep-blocked{padding:10px 12px;background:var(--amber-bg);border:1px solid #5f4a24;
  border-radius:10px;font-size:12px;color:#e3c78f}.dep-blocked b{color:var(--amber)}
.dep-list{list-style:none;padding:0;margin:0;display:grid;gap:7px}
.dep-list span{font-family:var(--mono);font-size:10px;color:var(--amber)}
.gate{font-size:11px;font-weight:700;padding:6px 10px;border-radius:999px}
.gate.pass{color:var(--green);background:var(--green-bg)}.gate.fail{color:var(--amber);background:var(--amber-bg)}
.conditions{display:grid;gap:7px}
.condition{display:flex;gap:11px;padding:9px 11px;border-radius:10px;background:#111821;
  border:1px solid var(--line)}.condition .mark{font-weight:900;color:var(--green)}
.condition.fail .mark{color:var(--red)}.condition b{font-size:12.5px}
.condition p{margin:2px 0 0;font-size:11.5px;color:var(--muted)}
.condition.fail{border-color:#4c2930}
.visual-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
figure{margin:0}figcaption{display:flex;justify-content:space-between;gap:12px;
  margin-bottom:8px;font-size:12px}figcaption span{color:var(--muted)}
.image-box{position:relative;background:#080b0f;border:1px solid var(--line);
  border-radius:12px;overflow:hidden;line-height:0}
.image-box img{width:100%;height:100%;object-fit:fill}
.image-box.partial{box-shadow:inset 0 0 0 2px #9c56aa55}
.tile-box{position:absolute;border:1px solid #ffffff77;color:#fff;font:700 10px var(--mono);
  padding:4px;text-shadow:0 1px 3px #000;background:#00000018;line-height:1.2}
.tile-box.reference{border-color:#ffffff44;background:#00000010}
.tile-box.missing{border-color:#ff86f0;background:repeating-linear-gradient(135deg,
  #b84dc31f 0 6px,#0000 6px 13px);color:#ffc8fa}
.mask-legend{display:flex;align-items:center;gap:9px;color:var(--muted);font-size:11.5px;
  margin:10px 0 14px}.swatch{width:26px;height:12px;border-radius:3px;
  background:repeating-linear-gradient(135deg,#411e49 0 4px,#652b6f 4px 8px)}
.ok-legend{color:var(--green)}
.compact{margin-top:8px}
.blocked-visual{border-color:#4c3b22;background:linear-gradient(135deg,#1a1813,var(--panel) 55%)}
.blocked-visual>p{color:#c7b89d;max-width:880px}
.reason-list{color:#e3c78f;font-size:12.5px}.reason-list li{margin:5px 0}
.warnings{color:var(--muted);font-size:12px;padding-left:18px}
.fine{color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:12px}
.kv{display:grid;grid-template-columns:1.15fr .85fr;margin:0;font-size:12.5px}
.kv dt,.kv dd{margin:0;padding:7px 0;border-bottom:1px solid #222d39}
.kv dt{color:var(--muted);font-family:var(--mono);font-size:11px}.kv dd{text-align:right}
footer{display:flex;justify-content:space-between;gap:20px;color:var(--muted);font-size:11.5px;
  border-top:1px solid var(--line);margin-top:24px;padding-top:18px}
footer p{margin:0}code{font-family:var(--mono);color:var(--cyan)}
@media(max-width:1000px){.cards{grid-template-columns:repeat(3,1fr)}
  .grid2,.visual-grid{grid-template-columns:1fr}.hero{display:block}
  .status{margin-top:22px;min-width:0}main{padding:24px 16px}}
@media(max-width:600px){.cards{grid-template-columns:repeat(2,1fr)}
  .notice{display:block}footer{display:block}.hint{text-align:left}}
"""


JS = """
const data=JSON.parse(document.getElementById('report-data').textContent);
const nodes=new Map([...document.querySelectorAll('.tile-node')].map(n=>[Number(n.dataset.tile),n]));
document.querySelectorAll('tr[data-tile]').forEach(row=>{
  row.addEventListener('mouseenter',()=>{
    const node=nodes.get(Number(row.dataset.tile)); if(node) node.classList.add('focus');
  });
  row.addEventListener('mouseleave',()=>{
    const node=nodes.get(Number(row.dataset.tile)); if(node) node.classList.remove('focus');
  });
});
console.info('HEVC tile report', data.status, data.available_tiles);
"""
