"""Markdown summary for a completed sweep."""

from __future__ import annotations

from pathlib import Path


def _fmt(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def render_summary(data: dict, output_path: str | Path) -> Path:
    results = data["results"]
    window = 320 if 320 in data.get("windows", []) else max(data["windows"])
    lines = [
        "# LPC 语音编码实验结果",
        "",
        f"生成时间：{data.get('generated_utc', '—')}",
        "",
        "## 实验设置",
        "",
        f"- 采样率：16 kHz，帧长 {data['frame_length']} 样本（25 ms），非重叠帧，汉宁窗。",
        f"- 系数：反射系数均匀量化，{data['coefficient_bits']} bit/系数。",
        f"- 残差：逐帧 float32 块缩放 + {data['residual_bits']} bit 均匀量化。",
        f"- 对比阶数：{', '.join(str(value) for value in data['orders'])}；"
        f"窗长：{', '.join(str(value) for value in data['windows'])} 样本。",
        "- 编码端以量化预测器和已重建历史生成残差（闭环），解码端使用同一预测器。",
        "",
        f"## 默认 {window} 样本窗长：阶数对比",
        "",
    ]
    for signal_name in dict.fromkeys(row["signal"] for row in results):
        rows = sorted(
            (
                row
                for row in results
                if row["signal"] == signal_name and row["window_length"] == window
            ),
            key=lambda row: row["order"],
        )
        if not rows:
            continue
        lines.extend(
            [
                f"### {rows[0]['label']}",
                "",
                "| 阶数 | 重建 SNR (dB) | MSE | 最大误差 | 系数 B | 残差 B | 总码率 (kbit/s) | 压缩比 | 最大极点 |",
                "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in rows:
            lines.append(
                "| {order} | {snr} | {mse} | {maxerr} | {coef} | {res} | {rate} | {ratio} | {pole} |".format(
                    order=row["order"],
                    snr=_fmt(row["snr_db"]),
                    mse=f"{row['mse']:.3e}",
                    maxerr=f"{row['max_abs_error']:.3e}",
                    coef=row["coefficient_file_bytes"],
                    res=row["residual_file_bytes"],
                    rate=_fmt(row["total_kilobits_per_second"], 1),
                    ratio=_fmt(row["compression_ratio"], 2),
                    pole=_fmt(row["stability"]["max_quantized_pole_radius"], 6),
                )
            )
        best_snr = max(rows, key=lambda row: row["snr_db"])
        cheapest = min(rows, key=lambda row: row["total_bytes"])
        lines.extend(
            [
                "",
                f"最高 SNR：{best_snr['order']} 阶（{_fmt(best_snr['snr_db'])} dB）；"
                f"最小码量：{cheapest['order']} 阶（{cheapest['total_bytes']} B，"
                f"{_fmt(cheapest['total_kilobits_per_second'], 1)} kbit/s）。",
                "",
            ]
        )

    lines.extend(
        [
            "## 稳定性审计",
            "",
            "| 信号 | 量化 | 失稳帧 | 稳定化 | 拒绝 | 最大极点 | 方法 | 静音输出峰值 |",
            "|---|---|---:|---:|---:|---:|---|---:|",
        ]
    )
    for audit in data.get("stability_audit", {}).get("audits", []):
        methods = ", ".join(
            f"{name}×{count}" for name, count in audit["methods"].items()
        )
        peak = (
            "—"
            if audit["silence_output_peak"] is None
            else f"{audit['silence_output_peak']:.6f}"
        )
        lines.append(
            "| {label} | {mode} {bits}b | {unstable} | {stabilized} | {rejected} | "
            "{radius:.6f} | {methods} | {peak} |".format(
                label=audit["label"],
                mode="反射" if audit["mode"] == "rc" else "直接",
                bits=audit["coefficient_bits"],
                unstable=audit["unstable_detected"],
                stabilized=audit["stabilized"],
                rejected=audit["rejected"],
                radius=audit["max_quantized_pole_radius"],
                methods=methods or "—",
                peak=peak,
            )
        )
    lines.extend(
        [
            "",
            "全零静音帧满足 `r[0] <= 1e-20`，直接返回零预测器，不进入 Levinson 除法；"
            "重建输出峰值保持 0。直接系数量化会逐帧检查极点，失稳时按 0.95 步长收缩系数，"
            "若仍失稳则拒绝为全零预测器，方法写入码流 flags 与审计数据。",
            "",
            "## 交付文件",
            "",
            "- `artifacts/lpc_report.html`：交互式报告，可切换信号、阶数和窗长。",
            "- `artifacts/benchmark.json`：全部组合的逐帧分析、矩阵、极点与误差数据。",
            "- `artifacts/streams/*.coef`：量化系数流。",
            "- `artifacts/streams/*.res`：量化残差文件。",
            "- `artifacts/streams/*.recon.wav`：本工具重建结果。",
            "- `decode_stream.py`：无第三方依赖的单文件解码器。",
            "",
            "```bash",
            "python3 decode_stream.py artifacts/streams/vowel_o12.coef \\",
            "  artifacts/streams/vowel_o12.res /tmp/vowel_recon.wav",
            "```",
            "",
        ]
    )
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination
