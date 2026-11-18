# ADTS 流解析与分段工具

纯标准库 Python 实现的 ADTS (AAC Audio Data Transport Stream, ISO/IEC 13818-7)
解析器，支持带/不带 CRC 的帧头校验、多首拼接流的 ID3/ADTS 边界切分、
截断尾帧定位，以及可逐段回读的分段索引导出。

## 功能

- **帧头校验**：检查 12 位 sync word (`0xFFF`)、layer 字段、采样率索引
  （保留索引 `0xD-0xF` 报错）、`frame_length` 合法性。
- **profile / 采样率解析**：输出原始 2 位 profile、AAC Audio Object Type
  （`profile + 1`，如 LC=2）、采样率索引与具体 Hz。
- **CRC 头长度区分**：`protection_absent=1` 时头长 7 字节；为 0 时头长
  9 字节（含 2 字节 CRC），两种模式可在同一流中混用。
- **拼接流切分**：遇到 ID3v2 标签或相邻帧采样率/profile/声道变化时新开
  一段；ID3 标签完整归属于其后的音轨，不会吞掉相邻段的数据。
- **每段统计**：帧数、采样率、样本数（每 raw data block 1024 样本）与
  播放时长 `sample_count / sample_rate`。
- **截断处理**：声明长度超过 EOF 的尾帧按截断报告（含绝对偏移、可用字节、
  期望长度、缺失字节），不计入帧数、样本数与播放时长。
- **索引导出**：JSON 索引含每段 `start/end/playable_end`，可直接按偏移
  从原文件逐段切片并独立重新解析。

## 用法

```bash
# 生成测试样本（无需 ffmpeg，全部为合成帧）
python3 samples/make_samples.py

# 命令行解析并导出分段索引
python3 -m adts_parser.cli samples/concat_truncated_tail.aac --json index.json

# 运行测试
python3 -m unittest discover -s tests
```

Python API：

```python
from adts_parser import parse_stream

index = parse_stream(data, source="track.aac")
for seg in index.segments:
    print(seg.index, seg.sample_rate, seg.sample_count, seg.duration_seconds)
chunk = index.extract_segment_bytes(data, 0)  # 仅本段字节，含其 ID3
```

## 样本文件（`samples/`）

| 文件 | 说明 |
| --- | --- |
| `single_no_crc.aac` | 单轨、无 CRC（7 字节头） |
| `single_with_crc.aac` | 单轨、带 CRC（9 字节头） |
| `mixed_crc_modes.aac` | 同一流内 CRC/无 CRC 帧混用 |
| `concat_two_tracks_id3.aac` | 两段 ID3v2 + ADTS 拼接（44.1k 立体声 / 16k 单声道） |
| `concat_truncated_tail.aac` | 同上，末帧被截断，报告偏移 1254 |
| `param_change_boundary.aac` | 无 ID3，靠参数变化识别边界 |

## 模块结构

- `adts_parser/adts.py`：ADTS 帧头位域解析与常量。
- `adts_parser/id3.py`：ID3v2 头/synchsafe 尺寸/footer 标志解析。
- `adts_parser/parser.py`：流扫描、分段、截断报告、JSON 索引与回读。
- `adts_parser/cli.py`：命令行入口。
- `samples/make_samples.py`：确定性夹具生成器。
- `tests/`：21 个单元/集成测试。
