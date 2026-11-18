# 音频水印盲提取实验

## 方案（公开描述的频带能量量化）

- 帧长 4096 采样（44.1 kHz，约 93 ms），每帧承载 1 bit。
- 两个连续子带：A = 1.5–3 kHz，B = 3.5–5 kHz（留保护带避免窗谱泄漏混带）。
- 嵌入：把 `d = 10*log10(E_A/E_B)` 量化到间距 2Δ、错开 Δ 的两个交织格点
  （QIM），A/B 对称增减能量，整帧能量近似不变。
- 盲检测：无需原音频。16 bit 同步头做滑动相关（按重叠长度归一化，支持
  头部被截后的部分同步）；载荷 16 bit + CRC-8（非零初值，全零码字无效）；
  解码时对码段做帧率漂移假设搜索（±1.25%）以对抗轻度重采样。
- 增益不变性：能量比值与整体增益无关。

## 运行

    python3 run_experiment.py

## 产物

- `artifacts/wav/`：`source_clean.wav`、各强度 `marked_d*.wav`（16-bit PCM）
  及 Δ=3 dB 的三种扰动版本。
- `artifacts/csv/sweep.csv`：强度 × 扰动的 BER / CRC / 同步位置 / 平均余量。
- `artifacts/csv/per_bit_delta3.csv`：Δ=3 dB 下每个载荷/CRC 位的判决距离。
- `artifacts/csv/negative_controls.csv`：无水印素材的拒绝结果。
- `artifacts/figures/tradeoff.png`：强度–失真 / 强度–鲁棒性取舍曲线。
- `artifacts/experiment_log.txt`：完整控制台报告。

## 主要结论

- Δ = 3 dB（SNR ≈ 38.8 dB）在洁净、−4.4 dB 增益、+0.5% 重采样、截去
  0.45 s 开头四种条件下均零误码、CRC 通过。
- Δ ≤ 1 dB 几乎不可闻（SNR ≥ 51 dB）但抗扰动不足；CRC 会拒绝误码，
  不会输出错误消息。
- 无水印素材（原音频/另一段音乐/白噪声/静音）全部因 CRC 校验失败被拒绝，
  不会被强制解释为有效消息。
