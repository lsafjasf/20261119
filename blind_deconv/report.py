"""Static HTML report writer."""

from __future__ import annotations

import html
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .experiment import CaseResult


def _fmt(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def _uncertainty_text(label: str) -> str:
    return {"low": "低", "medium": "中", "high": "高"}[label]


def write_html_report(
    results: list["CaseResult"], payload: dict[str, object], path: Path
) -> None:
    psnr_gains = [
        result.reconstruction.psnr_db - result.blurred_baseline.psnr_db
        for result in results
    ]
    mean_position = float(np.mean([result.kernel.centroid_error_px for result in results]))
    high_uncertainty = sum(result.uncertainty.label == "high" for result in results)

    rows = []
    for result in results:
        label = _uncertainty_text(result.uncertainty.label)
        row_class = "warning" if result.uncertainty.label == "high" else ""
        gain = result.reconstruction.psnr_db - result.blurred_baseline.psnr_db
        rows.append(
            f"""
            <tr class="{row_class}">
              <td><a href="#case-{html.escape(result.spec.key)}">{html.escape(result.spec.label)}</a></td>
              <td>{html.escape(result.spec.category)}</td>
              <td>{_fmt(result.kernel.centroid_error_px)}</td>
              <td>{_fmt(result.kernel.peak_error_px, 1)}</td>
              <td>{_fmt(result.kernel.ncc, 3)}</td>
              <td>{_fmt(result.reconstruction.psnr_db)} <small>({gain:+.2f} dB)</small></td>
              <td>{_fmt(result.reconstruction.relative_error_pct, 1)}%</td>
              <td>{_fmt(result.reconstruction.ringing_index_pct, 1)}%</td>
              <td><span class="badge {result.uncertainty.label}">{label} · {_fmt(result.uncertainty.uncertainty_score)}</span></td>
            </tr>
            """
        )

    sections = []
    for result in results:
        uncertainty = result.uncertainty
        uncertainty_note = ""
        if uncertainty.label == "high":
            uncertainty_note = (
                "<p class='alert'><strong>低纹理警告：</strong>"
                "观测中几乎没有稳定边缘，多个初值给出明显不同的核。"
                "应把核形状与位置视为不可辨识，而不是只看单一点估计。"
                "本例重建图像已退回平滑观测，因此较高的 PSNR 不代表核恢复成功。</p>"
            )
        sections.append(
            f"""
            <section class="case" id="case-{html.escape(result.spec.key)}">
              <div class="case-heading">
                <div>
                  <h2>{html.escape(result.spec.label)}</h2>
                  <p class="muted">{html.escape(result.spec.notes)}</p>
                </div>
                <span class="badge {uncertainty.label}">uncertainty {_uncertainty_text(uncertainty.label)}</span>
              </div>
              {uncertainty_note}
              <div class="metric-grid">
                <div><span>核质心位置误差</span><strong>{_fmt(result.kernel.centroid_error_px)} px</strong></div>
                <div><span>核峰值位置误差</span><strong>{_fmt(result.kernel.peak_error_px, 1)} px</strong></div>
                <div><span>重建 PSNR</span><strong>{_fmt(result.reconstruction.psnr_db)} dB</strong></div>
                <div><span>相对重建误差</span><strong>{_fmt(result.reconstruction.relative_error_pct, 1)}%</strong></div>
                <div><span>振铃指标</span><strong>{_fmt(result.reconstruction.ringing_index_pct, 1)}%</strong></div>
                <div><span>有效核质心标准差</span><strong>{_fmt(uncertainty.centroid_std_px)} px</strong></div>
              </div>
              <a href="figures/{html.escape(result.spec.key)}.png">
                <img src="figures/{html.escape(result.spec.key)}.png" alt="{html.escape(result.spec.label)} diagnostics">
              </a>
            </section>
            """
        )

    report = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>二维盲运动去卷积实验</title>
  <style>
    :root {{ color-scheme: light; --ink:#101828; --muted:#667085; --line:#e4e7ec;
             --blue:#175cd3; --red:#b42318; --amber:#dc6803; --green:#027a48; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:#f8fafc; color:var(--ink);
            font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC",sans-serif; }}
    main {{ max-width:1180px; margin:0 auto; padding:42px 24px 70px; }}
    h1 {{ font-size:34px; line-height:1.15; margin:0 0 10px; letter-spacing:-0.03em; }}
    h2 {{ font-size:22px; margin:0; }}
    h3 {{ margin-top:34px; }}
    p {{ margin:8px 0; }}
    a {{ color:var(--blue); text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
    .subtitle {{ color:var(--muted); font-size:17px; max-width:850px; }}
    .cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:28px 0; }}
    .card {{ background:white; border:1px solid var(--line); border-radius:12px; padding:16px; }}
    .card span {{ display:block; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.05em; }}
    .card strong {{ display:block; font-size:25px; margin-top:5px; }}
    .callout {{ background:#eff8ff; border:1px solid #b2ddff; border-radius:12px; padding:16px 18px; }}
    table {{ width:100%; border-collapse:collapse; background:white; border:1px solid var(--line);
             border-radius:12px; overflow:hidden; font-size:13px; }}
    th,td {{ padding:10px 11px; border-bottom:1px solid var(--line); text-align:left; white-space:nowrap; }}
    th {{ background:#f9fafb; color:#475467; font-size:11px; text-transform:uppercase; letter-spacing:.04em; }}
    tr:last-child td {{ border-bottom:0; }}
    tr.warning td {{ background:#fffbf5; }}
    .badge {{ display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:700; }}
    .badge.low {{ color:var(--green); background:#ecfdf3; }}
    .badge.medium {{ color:var(--amber); background:#fffaeb; }}
    .badge.high {{ color:var(--red); background:#fef3f2; }}
    .case {{ background:white; border:1px solid var(--line); border-radius:16px; padding:22px; margin:26px 0; }}
    .case-heading {{ display:flex; justify-content:space-between; gap:20px; align-items:flex-start; }}
    .case img {{ display:block; width:100%; height:auto; border:1px solid var(--line); border-radius:10px; margin-top:16px; }}
    .metric-grid {{ display:grid; grid-template-columns:repeat(6,1fr); gap:8px; margin-top:16px; }}
    .metric-grid div {{ border-radius:8px; background:#f9fafb; padding:9px 10px; }}
    .metric-grid span {{ display:block; color:var(--muted); font-size:11px; }}
    .metric-grid strong {{ display:block; margin-top:2px; font-size:15px; }}
    .muted {{ color:var(--muted); }}
    .alert {{ background:#fef3f2; border-left:3px solid var(--red); padding:10px 12px; margin:14px 0; }}
    .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:16px; }}
    .grid2 img {{ width:100%; border:1px solid var(--line); border-radius:10px; background:white; }}
    code {{ background:#f2f4f7; padding:2px 5px; border-radius:5px; font-size:13px; }}
    @media (max-width:850px) {{
      .cards {{ grid-template-columns:1fr 1fr; }}
      .metric-grid {{ grid-template-columns:1fr 1fr 1fr; }}
      .grid2 {{ grid-template-columns:1fr; }}
      table {{ display:block; overflow-x:auto; }}
    }}
  </style>
</head>
<body>
<main>
  <h1>二维盲运动去卷积实验</h1>
  <p class="subtitle">从短直线与折线轨迹生成的模糊图中，交替估计非负、单位和的模糊核与清晰图。
  求解器只接收模糊图像与最大核尺寸；真实轨迹仅用于事后的误差评估与核对照。</p>

  <div class="cards">
    <div class="card"><span>样本数</span><strong>{len(results)}</strong></div>
    <div class="card"><span>平均核位置误差</span><strong>{_fmt(mean_position)} px</strong></div>
    <div class="card"><span>平均重建增益</span><strong>{np.mean(psnr_gains):+.2f} dB</strong></div>
    <div class="card"><span>高不确定性样本</span><strong>{high_uncertainty}</strong></div>
  </div>

  <div class="callout">
    <strong>信息边界。</strong> 生成阶段使用已知轨迹与清晰图；进入求解器的对象只有
    <code>blurred</code> 和 <code>max_kernel_size</code>。初值固定为中心各向同性高斯，
    不从轨迹、核尺寸真值或清晰图初始化。低纹理样本的核不可唯一辨识，报告使用多初值集成的
    核标准差、质心标准差和可观测性分数显示该不确定性。
  </div>

  <h2 style="margin-top:34px">汇总指标</h2>
  <table>
    <thead><tr>
      <th>样本</th><th>类型</th><th>质心误差 px</th><th>峰值误差 px</th><th>核 NCC</th>
      <th>重建 PSNR</th><th>相对误差</th><th>振铃</th><th>不确定性</th>
    </tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <p class="muted">PSNR 括号内为相对“仅输入模糊图”的增益。振铃指标是真实边缘邻近平坦区中，
  重建残差高通分量的 RMS（相对灰度满量程的百分比）。</p>

  <h2 style="margin-top:34px">目标函数与核演化</h2>
  <div class="grid2">
    <div><img src="figures/objective_curves.png" alt="objective curves"><p class="muted">六组样本的目标函数与数据项。</p></div>
    <div><img src="figures/kernel_evolution.png" alt="kernel evolution"><p class="muted">从盲初值到最终核的快照。</p></div>
  </div>

  <h2 style="margin-top:34px">不确定性</h2>
  <p>集成成员仅改变优化初值与正则强度，不改变输入，也不接触真值。展示的核标准差是
  “集成离散度与不可辨识下限”的合成包络；当图像几乎没有结构时，即使多个初值偶然收敛到
  相似中心核，核位置仍不可由观测约束。高不确定性不是“失败装饰”，而是数据不支持的结论。</p>
  <img src="figures/uncertainty_overview.png" alt="uncertainty overview"
       style="width:100%;border:1px solid var(--line);border-radius:10px;">

  <h2 style="margin-top:34px">逐样本诊断</h2>
  {''.join(sections)}

  <h2 style="margin-top:34px">方法摘要</h2>
  <p>1. 图像步：在频域求解带 Laplacian 高通正则的 Wiener 型反卷积，并以松弛系数限制潜像更新。</p>
  <p>2. 核步：用潜像与残差的相关计算梯度，结合梯度域残差，经 Adam 更新后投影到非负、和为 1 的单纯形；
  同时施加弱 TV、熵和支撑先验。</p>
  <p>3. 指标：核位置误差为核质心欧氏距离；重建误差使用有效内部区域的 PSNR 与相对 RMSE；
  振铃在真实边缘外侧的平坦环带中度量残差高通能量。</p>
  <p class="muted">随机种子、完整目标函数序列与逐轮核统计见 <a href="results.json">results.json</a>；
  表格数据见 <a href="metrics.csv">metrics.csv</a>。</p>
</main>
</body>
</html>
"""
    path.write_text(report, encoding="utf-8")
