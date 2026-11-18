# CPFSK 文本调制解调器

纯 Python 标准库实现（无需 numpy）的单声道连续相位 FSK 调制解调器，
把短文本编码为双频 WAV，并能从 WAV 中还原字节。

## 协议

| 字段 | 长度 | 说明 |
| --- | --- | --- |
| 前导 Preamble | 32 bit | 交替 `1010...`，用于信号检测与符号定时 |
| 同步字 Sync | 16 bit | `0xEB90`，标志帧头结束 |
| 长度 Length | 8 bit | 载荷字节数（1–255） |
| 载荷 Payload | 8×N bit | UTF-8 文本字节 |
| 校验 CRC-16 | 16 bit | CCITT（poly 0x1021, init 0xFFFF），覆盖长度字节+载荷 |

所有字段 MSB 优先。

## 调制参数

- 采样率 8000 Hz，16-bit PCM 单声道
- 符号时长 8 ms（64 采样点/符号，125 波特）
- bit 0 → 1200 Hz，bit 1 → 2200 Hz，符号间相位连续（CPFSK）

## 接收端

1. 对两个频率分别做复混频并累加，得到每个起始位置的符号能量（O(1) 查询）。
2. 在未知起点下，对（起始偏移 × 时钟拉伸）做二维网格搜索，
   用已知前导+同步字做相关，粗搜后两级细化。
3. 逐符号解调，早晚门（early-late）跟踪残余定时漂移。
4. 校验同步字、长度与 CRC-16；任何一步失败即拒收（`DecodeError`），
   解码结果绝不用发送端原文替代。

## 用法

```bash
# 编码为 WAV（可选加噪声/拉伸模拟信道）
python3 cpfsk_modem.py tx --text "Hello" -o tx.wav --noise 0.15 --stretch 0.004

# 从 WAV 解码
python3 cpfsk_modem.py rx -i tx.wav

# 环路测试：多帧统计误码率与校验失败
python3 cpfsk_modem.py demo --noise 0.35 --stretch 0.005 --trials 20
```

`--stretch` 为时长拉伸比例（如 0.003 = 拉长 0.3%），`--noise` 为
相对满幅的 AWGN 标准差。demo 输出：成功帧数、CRC 校验失败帧数、
同步失败帧数、载荷误码率（BER）。
