# 立体声 Mid/Side 可逆整数变换教学台

一个零依赖（仅 Python 3 标准库）的教学工作台，核心交付物是一对**严格互逆的
整数 lifting 变换**：16 位立体声 `(L, R)` 样本变换为整数 `(M, S)`，落盘为带
位宽元信息的 `.msi` 文件，逆变换逐样本、逐比特精确还原。

## 1. 声明：lifting 公式与舍入规则

前向变换（整数域，单样本）：

```
S = L - R
M = L + floor(S / 2)
```

逆变换（复用同一个折半量，无新增舍入）：

```
L = M - floor(S / 2)
R = L - S
```

舍入规则明确声明为 **向负无穷取整（floor）**：

```
floor(a / 2) = a // 2 = a >> 1        （算术右移）
```

它与 C 语言的“向零截断”整数除法不同，负奇数上的差异是可逆性的关键：

```
floor(-1 / 2) = -1        trunc(-1 / 2) = 0
```

可逆性证明（代入即可）：

```
M - floor(S/2) = L + floor(S/2) - floor(S/2) = L
L - S          = L - (L - R)                  = R
```

全程只有整数加减和移位，没有浮点、没有乘法，逆变换不引入任何第二次舍入。

## 2. 逐步计算示例

负奇数样本 `(L, R) = (-3, -2)`（`python3 mside.py trace -3 -2`）：

```
步骤1  S = L - R = -3 - (-2) = -1
       floor(S / 2) = floor(-1 / 2) = -1   # 向负无穷，不是 0
步骤2  M = L + floor(S / 2) = -3 + (-1) = -4
```

逆变换：

```
步骤1  floor(S / 2) = floor(-1 / 2) = -1
步骤2  L = M - floor(S / 2) = -4 - (-1) = -3
步骤3  R = L - S = -3 - (-1) = -2
```

16 位异号满幅 `(32767, -32768)`：

```
S = 65535,  floor(65535/2) = 32767,  M = 65534   # M、S 均为 17 位
逆变换精确还原 (32767, -32768)
```

## 3. 溢出所需的额外位

16 位有符号输入区间 `[-32768, 32767]` 下，理论极值：

| 量 | 表达式 | 取值区间 | 所需有符号位宽 |
|----|--------|----------|----------------|
| S | `L - R` | `[-65535, 65535]` | 17 位 |
| M | `L + floor(S/2)` | `[-65536, 65534]` | 17 位 |

- 输入每帧 32 bit（两条 16 位通道），理论最小输出 34 bit，**净增 2 个
  符号扩展位**（M、S 各 1 位）。
- 实际落盘按字节对齐：17 位值各占 3 字节小端补码，每帧 6 字节 = 48 bit，
  其中 14 bit 为对齐填充；声明位宽与实际位宽都写在文件元信息里。
- 实证：`verify` 子命令对边界组合 + 10 万随机 int16 样本做全量互逆检查，
  M、S 实测最大位宽均为 17 bit，零失败。

注意一个常见误解：`M` 不是 `(L+R)/2` 的平均值，而是 lifting 形式
`L + floor((L-R)/2)`，因此在异号满幅时同样会触达 17 位（如 ±65534/−65536）。

## 4. 与普通浮点矩阵的差异

普通浮点 Mid/Side 矩阵为：

```
Mf = (L + R) / 2          L' = round(Mf + Sf)
Sf = (L - R) / 2          R' = round(Mf - Sf)
```

本工作台用 `struct` 把每一步强制量化到 IEEE-754 的 f16/f32/f64 存储精度，
模拟真实浮点管线，实测差异：

| 维度 | 整数 lifting（本台） | 浮点矩阵 |
|------|----------------------|----------|
| 数据类型 | 纯整数，无指数/尾数 | 浮点数，Mf/Sf 常带 `.5` 小数（如 32767.5） |
| 舍入 | 一次 floor，规则显式声明 | 每次运算都量化，重建再 round，误差来源多 |
| f16 行为 | 不受影响，严格互逆 | 5000 随机样本约 99% 失败；极值处 `Sf=65535/2` 溢出为 `inf`（位模式 `0x7C00`） |
| f32/f64 | 严格互逆 | int16 域内可精确往返，但每值至少占 4/8 字节且存储的是浮点 |
| 位宽契约 | 文件显式声明 17 位、逐值校验 | 依赖浮点格式隐式表达，无整数位宽概念 |
| 可逆性证明 | 代数代入即证，与样本无关 | 仅在精度足够时经验成立 |

f16 最大有限值只有 65504，而 `S` 的极值是 65535：浮点方案在 16 位满幅
异号样本上直接溢出为 ±inf，信息不可逆丢失；整数方案同一输出只需 17 位。

## 5. MSI 文件格式（带位宽元信息）

全部小端，布局：

```
偏移   长度        内容
0      8 字节      魔数 b"MSID0001"
8      4 字节      元信息 JSON 字节长度 H（uint32 LE）
12     H 字节      UTF-8 JSON 元信息
12+H   剩余        载荷：N 帧交错 [M0,S0,M1,S1,...]，
                   每值为定长有符号小端补码，字节宽由声明位宽推出
```

元信息字段：`format`、`frame_count`、`input_width_bits`、
`mid_width_bits`、`side_width_bits`、`rounding`、`layout`，
以及可选的 `sample_rate`、`source`。

读取端的严格校验（任一不过即抛 `MSIFormatError` 拒绝读取）：

1. 魔数必须为 `MSID0001`；
2. 元信息长度合理且为合法 UTF-8 JSON，必需字段齐全、类型正确；
3. 位宽字段在 1..64 范围内，`frame_count` 非负；
4. 载荷长度必须等于 `frame_count × (ceil(mid_width/8) + ceil(side_width/8))`；
5. 逐值检查实际有符号位宽不超过声明位宽（位宽契约）。

`demo` 会在产物目录生成并现场拒绝以下坏文件：

| 文件 | 违约点 | 拒绝结果摘要 |
|------|--------|--------------|
| `bad_magic.msi` | 魔数被改 | 魔数不匹配 |
| `truncated.msi` | 载荷少 4 字节 | 载荷长度与元信息不一致 |
| `bad_count.msi` | 帧计数虚增 5 | 载荷长度与元信息不一致 |
| `bad_width16.msi` | 位宽声明为 16 | 帧字节数与载荷不符 |
| `missing_field.msi` | 删除 `side_width_bits` | 缺少必需字段 |
| `width_contract_violation.msi` | 3 字节容器内塞入 18 位值 90000、仍声明 17 位 | 实际需要 18 位，位宽契约违约 |

## 6. 使用方法

```bash
# 完整教学演示：公式声明、逐步推导、位宽分析、浮点对照、落盘、坏文件拒绝
python3 mside.py demo --out artifacts/msi_demo

# 单样本逐步推导（可附浮点对照）
python3 mside.py trace -3 -2
python3 mside.py trace 32767 -32768 --float f16

# 文件级变换与精确还原
python3 mside.py encode input.wav output.msi
python3 mside.py decode output.msi restored.wav

# 互逆性与位宽验证 / 位宽分析
python3 mside.py verify
python3 mside.py widths
```

WAV 要求：16 位 PCM、立体声。演示产物位于 `artifacts/msi_demo/`，
完整运行记录见 `artifacts/demo_output.txt`。

## 7. 代码结构

`mside.py` 为单文件零依赖实现，按章节组织：

1. 核心 lifting：`half_floor` / `encode_sample` / `decode_sample`
2. 位宽工具：`signed_bit_width` / `required_signed_width`
3. 浮点对照：`float_matrix_roundtrip`（f16/f32/f64 量化模拟）
4. MSI 文件：`write_msi` / `read_msi` / `MSIFormatError`
5. 批处理与 WAV：`encode_stereo` / `decode_stereo` / `read_wav_stereo`
6. 教学输出：`trace_forward` / `trace_inverse` / `trace_float`
7. 验证与统计：`verify_roundtrip` / `width_report`
8. CLI：`demo` / `encode` / `decode` / `trace` / `verify` / `widths`
