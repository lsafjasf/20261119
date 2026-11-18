"""生成 HTML 分析报告: 全局波形、每个候选的 8 频能量图、
状态迁移表、混淆表、边界误差与拒识核对。"""
import base64
import html
import io
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

from .core import ALL_FREQS, ROW_FREQS, COL_FREQS, HOP, FRAME_LEN
from .evaluate import confusion_matrix, boundary_stats

_local_font = os.path.expanduser("~/.fonts/NotoSansSC.ttf")
if os.path.exists(_local_font):
    font_manager.fontManager.addfont(_local_font)
    plt.rcParams["font.sans-serif"] = ["Noto Sans SC", "DejaVu Sans"]
else:
    plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "WenQuanYi Zen Hei",
                                       "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def _fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _overview_fig(result, events, truth):
    t = np.arange(len(result.signal)) / result.fs
    fig, ax = plt.subplots(figsize=(13, 3.2))
    ax.plot(t, result.signal, lw=0.4, color="#4477aa")
    for item in truth:
        color = {"valid": "#2ca02c", "too_short": "#ff7f0e",
                 "overlap": "#d62728"}[item.kind]
        ax.axvspan(item.start_sample / result.fs, item.end_sample / result.fs,
                   color=color, alpha=0.15)
    for e in events:
        x = (e.start_sample + e.end_sample) / 2 / result.fs
        if e.status == "accepted":
            ax.text(x, 1.05, e.digit, ha="center", color="#2ca02c",
                    fontsize=11, weight="bold")
        else:
            ax.text(x, -1.15, "x" + e.digit, ha="center", color="#d62728",
                    fontsize=9)
    ax.set_ylim(-1.35, 1.35)
    ax.set_xlabel("时间 (s)")
    ax.set_title("波形总览: 绿=有效真值 橙=过短 红=重叠; 上方绿字=接受, 下方红x=拒识")
    return fig


def _event_fig(result, frames, ev, fs):
    """单个候选事件窗口内的 8 频 Goertzel 能量曲线。"""
    f0 = max(0, ev.start_sample // HOP - 6)
    f1 = min(len(frames), ev.end_sample // HOP + 6)
    times = [frames[i].time_ms / 1000.0 for i in range(f0, f1)]
    energies = np.array([frames[i].energies for i in range(f0, f1)])
    energies_db = 10 * np.log10(energies + 1e-12)

    fig, axes = plt.subplots(2, 1, figsize=(9, 4.6), sharex=True,
                             gridspec_kw={"height_ratios": [1, 2]})
    seg = result.signal[f0 * HOP: f1 * HOP + FRAME_LEN]
    tt = (np.arange(len(seg)) + f0 * HOP) / fs
    axes[0].plot(tt, seg, lw=0.5, color="#4477aa")
    axes[0].axvspan(ev.start_sample / fs, ev.end_sample / fs,
                    color="#2ca02c" if ev.status == "accepted" else "#d62728",
                    alpha=0.15)
    axes[0].set_title(f"候选 {ev.digit}  [{ev.status}] {ev.reason}  "
                      f"样本 {ev.start_sample}–{ev.end_sample}", fontsize=10)
    for k, f in enumerate(ALL_FREQS):
        group = "低频组" if k < 4 else "高频组"
        axes[1].plot(times, energies_db[:, k], lw=1.1,
                     label=f"{f:.0f}Hz ({group})")
    axes[1].set_ylabel("Goertzel 能量 (dB)")
    axes[1].set_xlabel("时间 (s)")
    axes[1].legend(fontsize=7, ncol=4, loc="lower left")
    axes[1].grid(alpha=0.3)
    return fig


def _esc(s):
    return html.escape(str(s))


def _confusion_html(ev):
    labels, mat = confusion_matrix(ev["confusion"])
    rows = ["<tr><th>真值 \\ 检测</th>"
            + "".join(f"<th>{_esc(c)}</th>" for c in labels) + "</tr>"]
    for r, row in zip(labels, mat):
        if not any(row):
            continue
        cells = []
        for c, v in zip(labels, row):
            cls = ""
            if v and r == c:
                cls = ' class="hit"'
            elif v:
                cls = ' class="err"'
            cells.append(f"<td{cls}>{v or ''}</td>")
        rows.append(f"<tr><th>{_esc(r)}</th>{''.join(cells)}</tr>")
    return "<table class='cm'><thead>" + rows[0] + "</thead><tbody>" \
        + "".join(rows[1:]) + "</tbody></table>"


def _boundary_html(ev):
    st = boundary_stats(ev["boundary"])
    if not st:
        return "<p>无匹配事件。</p>"
    head = ("<tr><th>数字</th><th>起点误差(ms)</th><th>终点误差(ms)</th></tr>")
    rows = "".join(
        f"<tr><td>{_esc(b['digit'])}</td>"
        f"<td>{b['start_err_ms']:+.1f}</td><td>{b['end_err_ms']:+.1f}</td></tr>"
        for b in ev["boundary"])
    summ = (f"<p>匹配 {st['n']} 个: 起点均值 {st['start_mean_ms']:+.1f} ms "
            f"(std {st['start_std_ms']:.1f}, 最大绝对 {st['start_maxabs_ms']:.1f}); "
            f"终点均值 {st['end_mean_ms']:+.1f} ms "
            f"(std {st['end_std_ms']:.1f}, 最大绝对 {st['end_maxabs_ms']:.1f})。</p>")
    return summ + f"<table><thead>{head}</thead><tbody>{rows}</tbody></table>"


def _events_html(events, fs):
    head = ("<tr><th>#</th><th>数字</th><th>起始样本</th><th>结束样本</th>"
            "<th>时长(ms)</th><th>结论</th><th>原因</th></tr>")
    rows = []
    for i, e in enumerate(events):
        dur = (e.end_sample - e.start_sample) / fs * 1000.0
        cls = "hit" if e.status == "accepted" else "err"
        rows.append(
            f"<tr><td>{i}</td><td>{_esc(e.digit)}</td>"
            f"<td>{e.start_sample}</td><td>{e.end_sample}</td>"
            f"<td>{dur:.0f}</td>"
            f"<td class='{cls}'>{'接受' if e.status=='accepted' else '拒识'}</td>"
            f"<td>{_esc(e.reason)}</td></tr>")
    return f"<table><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table>"


def _reject_check_html(ev):
    head = "<tr><th>数字</th><th>类型</th><th>说明</th><th>核对结果</th></tr>"
    rows = "".join(
        f"<tr><td>{_esc(c['truth'].digit)}</td><td>{_esc(c['truth'].kind)}</td>"
        f"<td>{_esc(c['truth'].note)}</td>"
        f"<td class='{'hit' if c['ok'] else 'err'}'>{_esc(c['note'])}</td></tr>"
        for c in ev["reject_check"])
    return f"<table><thead>{head}</thead><tbody>{rows}</tbody></table>"


def _transitions_html(ev):
    head = "<tr><th>帧</th><th>时间(ms)</th><th>从</th><th>到</th><th>原因</th></tr>"
    rows = "".join(
        f"<tr><td>{t['frame']}</td><td>{t['time_ms']}</td>"
        f"<td>{t['from']}</td><td>{t['to']}</td>"
        f"<td>{_esc(t['reason'])}</td></tr>"
        for t in ev.transitions)
    return (f"<table class='sm'><thead>{head}</thead>"
            f"<tbody>{rows}</tbody></table>")


CSS = """
body{font-family:sans-serif;margin:24px;color:#222;max-width:1100px}
h1{font-size:22px} h2{font-size:17px;margin-top:32px;border-bottom:2px solid #ccc;
padding-bottom:4px}
table{border-collapse:collapse;font-size:12px;margin:8px 0}
th,td{border:1px solid #bbb;padding:3px 8px;text-align:center}
td.err,td .err{color:#d62728;font-weight:bold}
td.hit{color:#2ca02c;font-weight:bold}
table.cm td.err{background:#ffd6d6} table.cm td.hit{background:#d9f2d9}
details{margin:10px 0;border:1px solid #ddd;border-radius:6px;padding:6px 10px}
summary{cursor:pointer;font-weight:bold}
img{max-width:100%;border:1px solid #eee;margin:6px 0}
.meta{color:#666;font-size:12px}
"""


def build_report(result, events, frames, transitions, noise_floor, ev, path):
    """汇总所有模块, 写出单一自包含 HTML 文件。"""
    parts = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
             "<title>DTMF 档案录音检测报告</title>",
             f"<style>{CSS}</style></head><body>",
             "<h1>DTMF 双音多频拨号检测报告</h1>",
             f"<p class='meta'>采样率 {result.fs} Hz, 帧长 {FRAME_LEN} 样本, "
             f"帧移 {HOP} 样本, 噪声底 {noise_floor:.3f}, "
             f"共 {len(frames)} 帧 / {len(events)} 个候选事件。</p>"]

    parts.append("<h2>波形总览</h2>")
    parts.append(f"<img src='data:image/png;base64,"
                 f"{_fig_to_b64(_overview_fig(result, events, result.truth))}'>")

    parts.append("<h2>检测事件一览</h2>")
    parts.append(_events_html(events, result.fs))

    parts.append("<h2>混淆表(行=真值, 列=检测, '-'=无)</h2>")
    parts.append(_confusion_html(ev))

    parts.append("<h2>边界误差</h2>")
    parts.append(_boundary_html(ev))

    parts.append("<h2>拒识核对(过短/重叠必须拒识)</h2>")
    parts.append(_reject_check_html(ev))

    parts.append("<h2>每个候选: 频率能量与状态迁移</h2>")
    for i, e in enumerate(events):
        dur = (e.end_sample - e.start_sample) / result.fs * 1000.0
        label = "接受" if e.status == "accepted" else "拒识"
        parts.append(f"<details><summary>#{i} {e.digit} — {label} "
                     f"({dur:.0f} ms, {_esc(e.reason)})</summary>")
        parts.append(f"<img src='data:image/png;base64,"
                     f"{_fig_to_b64(_event_fig(result, frames, e, result.fs))}'>")
        parts.append(_transitions_html(e))
        parts.append("</details>")

    parts.append("</body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(parts))
