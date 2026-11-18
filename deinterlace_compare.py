#!/usr/bin/env python3
"""去隔行算法对比实验: 单场复制 / 运动自适应 / 两场融合

- 合成 50fps 逐行参考序列(快速横移亮条 + 静止细横线 + 静止纹理)
- 分别按顶场优先(TFF)与底场优先(BFF)抽取场序列
- 三种策略逐场输出 50fps 全帧, 量化运动边缘重影与静止细线误差
- 输出场->帧对应表, 并校验场序颠倒导致的往返运动
"""
import csv
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

OUT = Path("artifacts")
H, W, T = 240, 320, 16          # 帧高/宽/场数(=输出帧数)
FPS = 50.0                       # 场率=输出帧率
BAR_W, BAR_V = 24, 8             # 亮条宽度与速度(px/场)
BAR_ROWS = slice(60, 180)


def synth_reference():
    """逐行参考序列: 渐变背景 + 1px 静止细横线 + 静止纹理 + 快速横移亮条."""
    frames = np.zeros((T, H, W), np.float64)
    frames[:] = 30 + 40 * np.arange(H)[:, None] / H
    thin_rows = (np.arange(H) % 24 == 4)
    frames[:, thin_rows, :] = 230
    frames[:, 150:230, 240:320] = 90
    frames[:, 160:220:2, 250:310:2] = 200
    bar_x = np.array([10 + BAR_V * t for t in range(T)])
    for t, x0 in enumerate(bar_x):
        frames[t, BAR_ROWS, x0:x0 + BAR_W] = 250
        frames[t, BAR_ROWS, x0 + BAR_W // 2:x0 + BAR_W] = 120
    return frames, thin_rows, bar_x


def deinterleave(frames, order):
    """逐行序列 -> 半高场序列. TFF: 偶数场取偶数行; BFF: 偶数场取奇数行."""
    fields, parity = [], []
    for i in range(len(frames)):
        top = (i % 2 == 0) if order == "tff" else (i % 2 == 1)
        fields.append(frames[i, 0::2] if top else frames[i, 1::2])
        parity.append("T" if top else "B")
    return fields, parity


def line_double(field, is_top):
    out = np.empty((H, W), np.float64)
    if is_top:
        out[0::2], out[1::2] = field, field
    else:
        out[1::2], out[0::2] = field, field
    return out


def deint_replicate(fields, parity):
    """单场复制(bob): 每场独立行复制, 不使用邻场."""
    return [line_double(fields[i], parity[i] == "T") for i in range(len(fields))]


def deint_blend(fields, parity):
    """两场融合: 当前场与下一场行复制后取平均."""
    n = len(fields)
    outs = []
    for i in range(n):
        j = i + 1 if i + 1 < n else i - 1
        a = line_double(fields[i], parity[i] == "T")
        b = line_double(fields[j], parity[j] == "T")
        outs.append(0.5 * a + 0.5 * b)
    return outs


def deint_adaptive(fields, parity, thr=12.0):
    """运动自适应: 前后同奇偶场差检测运动; 静止区交织邻场, 运动区单场复制."""
    n = len(fields)
    outs = []
    for i in range(n):
        ip = i - 1 if i > 0 else i + 1
        inx = i + 1 if i + 1 < n else i - 1
        cur = line_double(fields[i], parity[i] == "T")
        prev = line_double(fields[ip], parity[ip] == "T")
        nxt = line_double(fields[inx], parity[inx] == "T")
        motion = np.abs(prev - nxt) > thr
        woven = cur.copy()
        if parity[i] == "T":
            woven[1::2] = nxt[1::2]
        else:
            woven[0::2] = nxt[0::2]
        outs.append(np.where(motion, cur, woven))
    return outs


def weave_pair(fa, pa, fb, pb):
    """两场交织成一帧(用于梳齿演示与场序校验)."""
    out = np.empty((H, W), np.float64)
    for f, p in ((fa, pa), (fb, pb)):
        if p == "T":
            out[0::2] = f
        else:
            out[1::2] = f
    return out


def motion_band(x0):
    """亮条运动边缘带(条前后各 6px)."""
    band = np.zeros((H, W), bool)
    band[54:186, max(0, x0 - 6):min(W, x0 + BAR_W + 6)] = True
    return band


def ghost_metrics(outs, refs, bar_x):
    """运动边缘带内: MAE + 重影指数(残差与下一参考帧位移方向的相关性)."""
    maes, ghosts = [], []
    for i in range(1, len(outs) - 1):
        band = motion_band(bar_x[i])
        resid = outs[i] - refs[i]
        shift = refs[i + 1] - refs[i]
        maes.append(np.abs(resid[band]).mean())
        num = (resid[band] * shift[band]).sum()
        den = np.sqrt((resid[band] ** 2).sum() * (shift[band] ** 2).sum()) + 1e-9
        ghosts.append(num / den)
    return float(np.mean(maes)), float(np.mean(ghosts))


def thinline_metrics(outs, refs, thin_rows):
    """静止细线区(避开亮条与纹理块): MAE + 时间闪烁(逐像素时间标准差)."""
    cols = np.zeros(W, bool)
    cols[170:236] = True
    mask = thin_rows[:, None] & cols[None, :]
    stack = np.stack(outs)[:, mask]
    ref = np.stack(refs)[:, mask]
    mae = float(np.abs(stack - ref).mean())
    flicker = float(stack.std(axis=0).mean())
    return mae, flicker


def bar_centroid_x(frame):
    region = frame[BAR_ROWS, :]
    ys, xs = np.nonzero(region > 240)
    return float(xs.mean()) if len(xs) else np.nan


def field_order_check(fields, parity):
    """正确场序 vs 主客场颠倒(对内交换)后的场序列分别 bob 输出,
    统计亮条质心方向反转次数, 验证颠倒场序导致往返运动."""
    res = {}
    for name, swap in (("correct", False), ("swapped", True)):
        seq = list(range(len(fields)))
        if swap:
            for k in range(0, len(seq) - 1, 2):
                seq[k], seq[k + 1] = seq[k + 1], seq[k]
        cents = []
        for out_i, src in enumerate(seq):
            frame = line_double(fields[src], parity[src] == "T")
            cents.append(bar_centroid_x(frame))
        cents = np.array(cents)
        reversals = int(np.sum(np.diff(np.sign(np.diff(cents))) != 0))
        res[name] = (cents, reversals, seq)
    return res


def to_img(frame):
    return Image.fromarray(np.clip(frame, 0, 255).astype(np.uint8), "L")


def save_compare_sheet(path, refs, results, fields, parity, idx):
    """对比图: 行=参考/复制/自适应/融合/交织演示, 列=全帧/运动区放大/细线区放大."""
    x0 = 10 + BAR_V * idx
    motion_crop = (slice(50, 190), slice(max(0, x0 - 30), min(W, x0 + BAR_W + 30)))
    line_crop = (slice(0, 120), slice(160, 250))
    j = idx + 1 if idx + 1 < len(fields) else idx - 1
    weave = weave_pair(fields[idx], parity[idx], fields[j], parity[j])
    rows = [("ref", refs[idx]), ("weave", weave)]
    rows += [(name, outs[idx]) for name, outs in results.items()]
    zoom = 3
    label_w, head_h = 90, 16

    def crop_img(fr, sl):
        part = fr[sl]
        return to_img(part).resize(
            (part.shape[1] * zoom, part.shape[0] * zoom), Image.NEAREST)

    tiles = [(name, to_img(fr), crop_img(fr, motion_crop), crop_img(fr, line_crop))
             for name, fr in rows]
    cw = label_w + W + tiles[0][2].width + tiles[0][3].width + 20
    ch = head_h + sum(max(t[1].height, t[2].height, t[3].height)
                      for t in tiles) + 10 * len(tiles)
    sheet = Image.new("L", (cw, ch), 0)
    draw = ImageDraw.Draw(sheet)
    for x, head in ((label_w, "full"), (label_w + W + 10, "motion x3"),
                    (label_w + W + 10 + tiles[0][2].width + 10, "thinline x3")):
        draw.text((x, 2), head, fill=255)
    y = head_h
    for name, full, mc, lc in tiles:
        draw.text((2, y + 4), name, fill=255)
        sheet.paste(full, (label_w, y))
        sheet.paste(mc, (label_w + W + 10, y))
        sheet.paste(lc, (label_w + W + 10 + mc.width + 10, y))
        y += max(full.height, mc.height, lc.height) + 10
    sheet.save(path)


METHODS = {
    "replicate": deint_replicate,
    "adaptive": deint_adaptive,
    "blend": deint_blend,
}


def run_order(order, refs, thin_rows, bar_x):
    fields, parity = deinterleave(refs, order)
    report = [f"===== 输入场序: {'顶场优先(TFF)' if order == 'tff' else '底场优先(BFF)'} "
              f"| 场率 {FPS:.0f} 场/秒 | {T} 场 -> {T} 帧 ====="]
    results, timings, metrics, csv_rows = {}, {}, {}, []
    for name, fn in METHODS.items():
        t0 = time.perf_counter()
        outs = fn(fields, parity)
        dt = time.perf_counter() - t0
        results[name] = outs
        timings[name] = dt
        g_mae, g_idx = ghost_metrics(outs, refs, bar_x)
        l_mae, l_flk = thinline_metrics(outs, refs, thin_rows)
        metrics[name] = (g_mae, g_idx, l_mae, l_flk)
        report.append(
            f"[{name:9s}] 耗时 {dt*1e3:7.2f} ms ({dt/T*1e3:5.2f} ms/帧, "
            f"{T/dt:7.1f} fps) | 运动边缘 MAE {g_mae:6.2f} 重影指数 {g_idx:+.3f} "
            f"| 细线 MAE {l_mae:6.2f} 闪烁 {l_flk:6.2f}")
        n = len(fields)
        for i in range(n):
            if name == "replicate":
                srcs = [i]
            elif name == "blend":
                srcs = [i, i + 1 if i + 1 < n else i - 1]
            else:
                srcs = sorted({i, i - 1 if i > 0 else i + 1,
                               i + 1 if i + 1 < n else i - 1})
            csv_rows.append({"order": order, "method": name, "out_frame": i,
                             "time_ms": f"{i / FPS * 1000:.1f}",
                             "src_fields": "+".join(str(s) for s in srcs)})
    chk = field_order_check(fields, parity)
    for tag, (cents, rev, seq) in chk.items():
        report.append(f"场序校验[{tag}] 方向反转 {rev} 次, 质心x="
                      + ",".join(f"{c:.0f}" for c in cents))
    save_compare_sheet(OUT / f"compare_{order}.png", refs, results,
                       fields, parity, idx=9)
    return report, csv_rows, metrics


def main():
    OUT.mkdir(exist_ok=True)
    refs, thin_rows, bar_x = synth_reference()
    to_img(refs[9]).save(OUT / "reference_frame9.png")
    all_report, all_rows = [], []
    for order in ("tff", "bff"):
        report, rows, _ = run_order(order, refs, thin_rows, bar_x)
        all_report += report + [""]
        all_rows += rows
    text = "\n".join(all_report)
    (OUT / "report.txt").write_text(text, encoding="utf-8")
    with open(OUT / "field_frame_map.csv", "w", newline="",
              encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "order", "method", "out_frame", "time_ms", "src_fields"])
        writer.writeheader()
        writer.writerows(all_rows)
    print(text)


if __name__ == "__main__":
    main()
