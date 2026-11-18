#!/usr/bin/env python3
"""
mside.py —— 立体声 Mid/Side 可逆整数变换教学台

核心交付物是一对严格互逆的整数 lifting 变换（前向 / 逆变换），
全程只使用整数运算，不依赖任何第三方库。

舍入规则（本工作台明确声明）
----------------------------
对所有“除以 2”使用向负无穷取整（floor division），即

        q(a) = floor(a / 2) = a >> 1          （算术右移）

Python 的 `a // 2` 对负数也向负无穷取整，因此与算术右移完全一致。
注意它和“向零截断”（C 语言整数除法）不同：

        floor((-1) / 2) = -1     trunc((-1) / 2) = 0

正是这条舍入规则保证了 lifting 的严格可逆性。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys
import wave


# ---------------------------------------------------------------------------
# 1. 核心：带 floor 舍入的整数 lifting 公式
# ---------------------------------------------------------------------------

def half_floor(value: int) -> int:
    """floor(value / 2)：向负无穷取整的折半（算术右移 value >> 1）。"""
    return value // 2


def encode_sample(left: int, right: int) -> tuple[int, int]:
    """
    前向 lifting（单样本，整数域）：

        S = L - R
        M = L + floor(S / 2) = L + (L - R) // 2

    返回 (M, S)。M 为“中（lifted mid）”，S 为“侧（side/difference）”。
    """
    side = left - right
    mid = left + half_floor(side)
    return mid, side


def decode_sample(mid: int, side: int) -> tuple[int, int]:
    """
    逆 lifting（单样本），与 encode_sample 严格互逆：

        L = M - floor(S / 2)
        R = L - S

    逆变换不含任何新的舍入，复用同一个 floor(S/2)，故逐样本精确还原。
    """
    left = mid - half_floor(side)
    right = left - side
    return left, right


# ---------------------------------------------------------------------------
# 2. 位宽工具
# ---------------------------------------------------------------------------

def signed_bit_width(value: int) -> int:
    """
    容纳该有符号整数所需的补码位宽 n（区间 -2^(n-1) .. 2^(n-1)-1）。
    约定 0 用 1 位表示。
    """
    if value == 0:
        return 1
    if value > 0:
        return value.bit_length() + 1
    return (-value - 1).bit_length() + 1


def required_signed_width(min_value: int, max_value: int) -> int:
    """容纳整个取值区间所需的最小有符号位宽（全零区间按 1 位计）。"""
    width = 1
    if max_value > 0:
        width = max(width, max_value.bit_length() + 1)
    if min_value < 0:
        width = max(width, (-min_value - 1).bit_length() + 1)
    return width


INT16_MIN, INT16_MAX = -32768, 32767

# 16 位输入下 M、S 的理论极值（由 encode_sample 在四角样本上验证）：
#   S = L - R              ∈ [-65535, 65535] -> 17 位有符号
#   M = L + floor((L-R)/2) ∈ [-65536, 65534] -> 17 位有符号
# 两条 16 位通道（合计 32 bit）变换后需要 17 + 17 = 33 bit，
# 即整个立体声帧净增 1 个溢出保护位（见 README 的位宽章节）。
S_RANGE = (INT16_MIN - INT16_MAX, INT16_MAX - INT16_MIN)
M_RANGE = (2 * INT16_MIN, INT16_MAX - INT16_MIN - 1)


# ---------------------------------------------------------------------------
# 3. 对照：普通浮点矩阵 M = (L+R)/2, S = (L-R)/2
# ---------------------------------------------------------------------------

# 用 struct 把每次运算结果强制量化到指定 IEEE-754 精度，
# 模拟“全程用 f16 / f32 / f64 计算并存储”的普通浮点矩阵方案。
_FLOAT_FORMATS = {"f16": "e", "f32": "f", "f64": "d"}


def _quantize(value: float, fmt: str) -> float:
    try:
        return struct.unpack(fmt, struct.pack(fmt, value))[0]
    except OverflowError:
        # 模拟硬件浮点：超出可表示范围时饱和为 ±inf（f16 最大有限值仅 65504）
        return float("inf") if value >= 0 else float("-inf")


def float_matrix_roundtrip(left: int, right: int, precision: str) -> tuple[int, int, dict]:
    """
    普通浮点正交矩阵（Haar 归一化前的常用形式）：

        Mf = (L + R) / 2          L' = round(Mf + Sf)
        Sf = (L - R) / 2          R' = round(Mf - Sf)

    每一步都量化到 precision 指定的浮点精度后再存储，重建时四舍五入回整数。
    返回 (L', R', 诊断信息)。
    """
    fmt = _FLOAT_FORMATS[precision]
    fl, fr = _quantize(float(left), fmt), _quantize(float(right), fmt)
    mid = _quantize(_quantize(fl + fr, fmt) / 2.0, fmt)
    side = _quantize(_quantize(fl - fr, fmt) / 2.0, fmt)
    # 对负数做对称四舍五入（Python round 是银行家舍入，这里用对称形式），
    # 保证浮点方案拿到“最理想”的重建机会。
    rec_l = _symmetric_round(_quantize(mid + side, fmt))
    rec_r = _symmetric_round(_quantize(mid - side, fmt))
    overflow = math.isinf(mid) or math.isinf(side)
    diag = {
        "mid": mid,
        "side": side,
        "mid_repr": _float_repr(mid, precision),
        "side_repr": _float_repr(side, precision),
        "overflow": overflow,
    }
    return rec_l, rec_r, diag


def _symmetric_round(value: float) -> int:
    """对称四舍五入：+0.5 向下取整对正数，-0.5 向上取整对负数。"""
    if math.isinf(value):
        return INT16_MAX if value > 0 else INT16_MIN
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def _float_repr(value: float, precision: str) -> str:
    fmt = _FLOAT_FORMATS[precision]
    raw = struct.pack(fmt, value)
    (bits,) = struct.unpack({2: "H", 4: "I", 8: "Q"}[len(raw)], raw)
    width = len(raw) * 8
    return f"0x{bits:0{width // 4}X}（{precision} 存储位模式）"


# ---------------------------------------------------------------------------
# 4. 带位宽元信息的 MSI 文件格式
# ---------------------------------------------------------------------------
#
# 字节布局（全部小端）：
#
#   偏移  长度        内容
#   0     8 字节      魔数 b"MSID0001"
#   8     4 字节      元信息 JSON 长度 H（uint32 LE）
#   12    H 字节      UTF-8 JSON 元信息（含位宽等字段，见 _build_meta）
#   12+H  剩余        载荷：N 帧，每帧 M、S 各一个定长有符号小端整数
#
# 定长整数的字节宽度由元信息里的 mid_width/side_width 推出，
# 因此“位宽”是文件自描述的、逆变换前必须通过严格校验的契约。

MAGIC = b"MSID0001"


class MSIFormatError(Exception):
    """MSI 文件不合法（魔数、元信息、位宽契约或载荷长度不一致）时抛出。"""


def _bytes_for_width(bit_width: int) -> int:
    if bit_width < 1:
        raise MSIFormatError(f"位宽必须为正整数，收到 {bit_width}")
    return (bit_width + 7) // 8


def _pack_int(value: int, byte_count: int) -> bytes:
    """把有符号整数打包为 byte_count 字节小端补码；超出范围直接报错。"""
    limit = 1 << (8 * byte_count - 1)
    if not (-limit <= value < limit):
        raise MSIFormatError(
            f"值 {value} 超出 {byte_count * 8} 位有符号整数范围 "
            f"[{-limit}, {limit - 1}]，拒绝写入（请检查声明位宽）"
        )
    return int(value).to_bytes(byte_count, "little", signed=True)


def _unpack_int(raw: bytes) -> int:
    return int.from_bytes(raw, "little", signed=True)


def _build_meta(frame_count: int, mid_width: int, side_width: int,
                input_width: int, extra: dict | None = None) -> dict:
    meta = {
        "format": "MSID0001",
        "frame_count": frame_count,
        "input_width_bits": input_width,
        "mid_width_bits": mid_width,
        "side_width_bits": side_width,
        "rounding": "floor_divide_by_2 (arithmetic right shift, toward -infinity)",
        "layout": "interleaved [M0,S0,M1,S1,...], signed little-endian two's complement",
    }
    if extra:
        meta.update(extra)
    return meta


def write_msi(path: str, frames: list[tuple[int, int]], input_width: int = 16,
              mid_width: int = 17, side_width: int = 17,
              extra_meta: dict | None = None) -> dict:
    """
    把 (M, S) 帧列表写成 MSI 文件。写入前逐值校验是否落在声明位宽内，
    返回实际写入的元信息字典。
    """
    meta = _build_meta(len(frames), mid_width, side_width, input_width, extra_meta)
    mid_bytes = _bytes_for_width(mid_width)
    side_bytes = _bytes_for_width(side_width)
    payload = bytearray()
    for index, (mid, side) in enumerate(frames):
        try:
            payload += _pack_int(mid, mid_bytes)
            payload += _pack_int(side, side_bytes)
        except MSIFormatError as exc:
            raise MSIFormatError(f"第 {index} 帧写入失败：{exc}") from exc
    meta_raw = json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8")
    with open(path, "wb") as handle:
        handle.write(MAGIC)
        handle.write(struct.pack("<I", len(meta_raw)))
        handle.write(meta_raw)
        handle.write(payload)
    return meta


def _read_exact(handle, size: int, what: str) -> bytes:
    raw = handle.read(size)
    if len(raw) != size:
        raise MSIFormatError(f"读取 {what} 时文件被截断：需要 {size} 字节，实际 {len(raw)} 字节")
    return raw


def read_msi_header(path: str) -> tuple[dict, int]:
    """只读取并校验头部，返回 (元信息, 载荷起始偏移)。"""
    if not os.path.isfile(path):
        raise MSIFormatError(f"文件不存在：{path}")
    with open(path, "rb") as handle:
        magic = _read_exact(handle, len(MAGIC), "魔数")
        if magic != MAGIC:
            raise MSIFormatError(
                f"魔数不匹配：期望 {MAGIC!r}，实际 {magic!r}，这不是 MSID0001 文件"
            )
        (meta_len,) = struct.unpack("<I", _read_exact(handle, 4, "元信息长度"))
        if meta_len <= 0 or meta_len > 1 << 20:
            raise MSIFormatError(f"元信息长度字段非法：{meta_len}")
        meta_raw = _read_exact(handle, meta_len, "元信息 JSON")
        try:
            meta = json.loads(meta_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MSIFormatError(f"元信息不是合法 UTF-8 JSON：{exc}") from exc
        _validate_meta(meta)
        return meta, len(MAGIC) + 4 + meta_len


def _validate_meta(meta: dict) -> None:
    required = {
        "format": str,
        "frame_count": int,
        "input_width_bits": int,
        "mid_width_bits": int,
        "side_width_bits": int,
    }
    for key, kind in required.items():
        if key not in meta:
            raise MSIFormatError(f"元信息缺少必需字段：{key!r}")
        if not isinstance(meta[key], kind) or isinstance(meta[key], bool):
            raise MSIFormatError(f"元信息字段 {key!r} 类型错误：期望 {kind.__name__}")
    if meta["format"] != "MSID0001":
        raise MSIFormatError(f"format 标识不匹配：{meta['format']!r}")
    if meta["frame_count"] < 0:
        raise MSIFormatError("frame_count 不能为负数")
    for key in ("input_width_bits", "mid_width_bits", "side_width_bits"):
        if meta[key] < 1 or meta[key] > 64:
            raise MSIFormatError(f"字段 {key}={meta[key]} 超出允许范围 1..64")


def read_msi(path: str) -> tuple[list[tuple[int, int]], dict]:
    """
    读取并完整校验 MSI 文件，返回 (帧列表, 元信息)。
    校验载荷长度与帧计数/位宽严格一致，并复核每个值落在声明位宽内。
    """
    meta, payload_offset = read_msi_header(path)
    mid_bytes = _bytes_for_width(meta["mid_width_bits"])
    side_bytes = _bytes_for_width(meta["side_width_bits"])
    frame_size = mid_bytes + side_bytes
    expected_len = frame_size * meta["frame_count"]
    with open(path, "rb") as handle:
        handle.seek(payload_offset)
        payload = handle.read()
    trailing = len(payload) - expected_len
    if trailing != 0:
        raise MSIFormatError(
            f"载荷长度与元信息不一致：声明 {meta['frame_count']} 帧 × "
            f"{frame_size} 字节应为 {expected_len} 字节，实际 {len(payload)} 字节"
            f"（{'多出' if trailing > 0 else '缺少'} {abs(trailing)} 字节），拒绝读取"
        )
    frames: list[tuple[int, int]] = []
    offset = 0
    for index in range(meta["frame_count"]):
        mid = _unpack_int(payload[offset:offset + mid_bytes])
        offset += mid_bytes
        side = _unpack_int(payload[offset:offset + side_bytes])
        offset += side_bytes
        # 位宽契约：值实际占用的有符号补码位宽不得超过声明位宽。
        # 仅检查容器范围会漏掉“宽值截断回绕成窄容器内合法值”的违约。
        if signed_bit_width(mid) > meta["mid_width_bits"]:
            raise MSIFormatError(
                f"第 {index} 帧 M={mid} 实际需要 {signed_bit_width(mid)} 位，"
                f"超过声明位宽 {meta['mid_width_bits']} 位，位宽契约违约，拒绝读取"
            )
        if signed_bit_width(side) > meta["side_width_bits"]:
            raise MSIFormatError(
                f"第 {index} 帧 S={side} 实际需要 {signed_bit_width(side)} 位，"
                f"超过声明位宽 {meta['side_width_bits']} 位，位宽契约违约，拒绝读取"
            )
        frames.append((mid, side))
    return frames, meta


# ---------------------------------------------------------------------------
# 5. 整帧批处理与 16 位立体声 WAV I/O
# ---------------------------------------------------------------------------

def encode_stereo(samples: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """对整条立体声轨道做前向整数 lifting。"""
    return [encode_sample(left, right) for left, right in samples]


def decode_stereo(frames: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """对整条 M/S 轨道做逆 lifting，逐样本还原 L/R。"""
    return [decode_sample(mid, side) for mid, side in frames]


def read_wav_stereo(path: str) -> tuple[list[tuple[int, int]], int]:
    """读取 16 位 PCM 立体声 WAV，返回 (样本对列表, 采样率)。"""
    with wave.open(path, "rb") as wav:
        channels = wav.getnchannels()
        sampwidth = wav.getsampwidth()
        rate = wav.getframerate()
        if channels != 2:
            raise ValueError(f"仅支持立体声 WAV，当前声道数 = {channels}")
        if sampwidth != 2:
            raise ValueError(f"仅支持 16 位 PCM，当前样本宽度 = {sampwidth * 8} 位")
        raw = wav.readframes(wav.getnframes())
    count = len(raw) // 2
    values = list(struct.unpack(f"<{count}h", raw))
    return [(values[i], values[i + 1]) for i in range(0, count, 2)], rate


def write_wav_stereo(path: str, samples: list[tuple[int, int]], rate: int) -> None:
    """把 16 位立体声样本写成 WAV，写入前校验范围。"""
    flat: list[int] = []
    for index, (left, right) in enumerate(samples):
        for name, value in (("L", left), ("R", right)):
            if not (INT16_MIN <= value <= INT16_MAX):
                raise ValueError(
                    f"第 {index} 帧 {name}={value} 超出 16 位范围，无法写入 WAV"
                )
        flat.extend((left, right))
    with wave.open(path, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(struct.pack(f"<{len(flat)}h", *flat))


def encode_wav_to_msi(wav_path: str, msi_path: str) -> dict:
    samples, rate = read_wav_stereo(wav_path)
    frames = encode_stereo(samples)
    mid_width = required_signed_width(
        min(mid for mid, _ in frames), max(mid for mid, _ in frames)
    ) if frames else 1
    side_width = required_signed_width(
        min(side for _, side in frames), max(side for _, side in frames)
    ) if frames else 1
    meta = write_msi(
        msi_path, frames, input_width=16,
        mid_width=mid_width, side_width=side_width,
        extra_meta={"sample_rate": rate, "source": os.path.basename(wav_path)},
    )
    return meta


# ---------------------------------------------------------------------------
# 6. 逐步计算的教学输出
# ---------------------------------------------------------------------------

def trace_forward(left: int, right: int) -> str:
    """单样本前向变换的逐步推导文本。"""
    diff = left - right
    halved = half_floor(diff)
    mid = left + halved
    lines = [
        f"输入  L = {left:>7d} (16 位),  R = {right:>7d} (16 位)",
        f"  步骤1  S = L - R = {left} - ({right}) = {diff}",
        f"         floor(S / 2) = floor({diff} / 2) = {halved}"
        f"   [算术右移 {diff} >> 1 = {halved}]",
        f"  步骤2  M = L + floor(S / 2) = {left} + ({halved}) = {mid}",
        f"  输出  M = {mid:>7d} ({signed_bit_width(mid)} 位),  "
        f"S = {diff:>7d} ({signed_bit_width(diff)} 位)",
    ]
    return "\n".join(lines)


def trace_inverse(mid: int, side: int) -> str:
    """单样本逆变换的逐步推导文本。"""
    halved = half_floor(side)
    left = mid - halved
    right = left - side
    lines = [
        f"输入  M = {mid:>7d},  S = {side:>7d}",
        f"  步骤1  floor(S / 2) = floor({side} / 2) = {halved}"
        f"   [复用前向同一舍入，无新增舍入]",
        f"  步骤2  L = M - floor(S / 2) = {mid} - ({halved}) = {left}",
        f"  步骤3  R = L - S = {left} - ({side}) = {right}",
        f"  还原  L = {left:>7d} (16 位),  R = {right:>7d} (16 位)",
    ]
    return "\n".join(lines)


def trace_float(left: int, right: int, precision: str) -> str:
    """普通浮点矩阵在同一样本上的逐步推导文本。"""
    rec_l, rec_r, diag = float_matrix_roundtrip(left, right, precision)
    ok = (rec_l, rec_r) == (left, right) and not diag["overflow"]
    lines = [
        f"[{precision}] 浮点矩阵  Mf = (L+R)/2,  Sf = (L-R)/2",
        f"  存储  Mf = {diag['mid']!r:>12}  {diag['mid_repr']}",
        f"        Sf = {diag['side']!r:>12}  {diag['side_repr']}",
        f"  重建  L' = round(Mf+Sf) = {rec_l},  R' = round(Mf-Sf) = {rec_r}",
        f"  结果  {'精确还原' if ok else '失败：' + _float_fail_reason(left, right, rec_l, rec_r, diag)}",
    ]
    return "\n".join(lines)


def _float_fail_reason(left: int, right: int, rec_l: int, rec_r: int, diag: dict) -> str:
    if diag["overflow"]:
        return (f"浮点溢出为 inf（存储位模式如上），"
                f"原值 ({left}, {right}) 的信息已丢失，饱和重建 ({rec_l}, {rec_r}) 不可作为无损证据")
    return f"舍入失真：原值 ({left}, {right}) ≠ 重建 ({rec_l}, {rec_r})"


# ---------------------------------------------------------------------------
# 7. 演示数据、正确性验证与位宽统计
# ---------------------------------------------------------------------------

def demo_samples() -> list[tuple[int, int]]:
    """覆盖 16 位极值与正/负奇数的教学样本集。"""
    return [
        (INT16_MAX, INT16_MIN),       # 异号满幅：最大溢出风险
        (INT16_MIN, INT16_MAX),       # 反向异号满幅
        (INT16_MAX, INT16_MAX),       # 同号满幅
        (INT16_MIN, INT16_MIN),
        (3, 2),                       # 正奇数差：floor(1/2)=0
        (-3, -2),                     # 负奇数差：floor(-1/2)=-1（关键舍入）
        (2, 3),
        (-2, -3),
        (1, 0),
        (0, -1),
        (-1, 1),
        (32767, 32766),               # 正奇数大值
        (-32768, -32767),             # 负奇数大值（含 int16 下界）
    ]


def make_demo_wav(path: str, rate: int = 48000) -> list[tuple[int, int]]:
    """生成一段短的 16 位立体声 WAV 作为文件级演示素材。"""
    pattern = demo_samples()
    repeat = 8
    samples = pattern * repeat
    write_wav_stereo(path, samples, rate)
    return samples


def verify_roundtrip(trials: int = 100_000, seed: int = 20261119) -> tuple[int, int, int]:
    """
    穷举边界 + 随机覆盖全 int16 域，验证整数 lifting 逐样本严格互逆，
    并统计 M/S 实际占用的最大位宽。返回 (试验数, M 最大位宽, S 最大位宽)。
    """
    rng = __import__("random").Random(seed)
    worst_m = worst_s = 1

    def check(left: int, right: int) -> None:
        nonlocal worst_m, worst_s
        mid, side = encode_sample(left, right)
        assert decode_sample(mid, side) == (left, right), (left, right)
        worst_m = max(worst_m, signed_bit_width(mid))
        worst_s = max(worst_s, signed_bit_width(side))

    edge = (INT16_MIN, -3, -2, -1, 0, 1, 2, 3, INT16_MAX)
    for left in edge:
        for right in edge:
            check(left, right)
    checked = len(edge) ** 2
    for _ in range(trials):
        check(rng.randint(INT16_MIN, INT16_MAX), rng.randint(INT16_MIN, INT16_MAX))
    return checked + trials, worst_m, worst_s


def width_report() -> str:
    """生成溢出额外位的理论与实证说明文本。"""
    total_in = 16 + 16
    total_out = required_signed_width(*M_RANGE) + required_signed_width(*S_RANGE)
    stored_bytes = _bytes_for_width(required_signed_width(*M_RANGE)) + \
        _bytes_for_width(required_signed_width(*S_RANGE))
    lines = [
        "位宽与溢出分析（16 位有符号输入）",
        f"  输入  L ∈ [{INT16_MIN}, {INT16_MAX}],  R 同；每帧 {total_in} bit",
        f"  S = L - R            ∈ [{S_RANGE[0]}, {S_RANGE[1]}]"
        f"  -> {required_signed_width(*S_RANGE)} 位有符号",
        f"  M = L + floor(S/2)   ∈ [{M_RANGE[0]}, {M_RANGE[1]}]"
        f"  -> {required_signed_width(*M_RANGE)} 位有符号",
        f"  理论最小：输出每帧 {total_out} bit，较输入净增 {total_out - total_in} bit"
        "（M、S 各需要 1 个符号扩展位）",
        f"  实际落盘：17 位值按字节对齐为 3 字节小端补码，每帧 {stored_bytes} 字节"
        f" = {stored_bytes * 8} bit（其中 {stored_bytes * 8 - total_out} bit 为对齐填充）",
        "  位宽由 MSI 元信息显式声明，逆变换前逐项校验",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 8. 命令行入口
# ---------------------------------------------------------------------------

def _print_header(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def run_demo(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    wav_path = os.path.join(out_dir, "demo_stereo.wav")
    msi_path = os.path.join(out_dir, "demo_stereo.msi")
    restored_path = os.path.join(out_dir, "restored.wav")

    _print_header("0. 声明：lifting 公式与舍入规则")
    print(__doc__.strip())

    _print_header("1. 逐步计算：16 位极值与正/负奇数样本")
    for left, right in demo_samples()[:6]:
        print(trace_forward(left, right))
        mid, side = encode_sample(left, right)
        print(trace_inverse(mid, side))
        print("-" * 72)

    _print_header("2. 位宽与溢出所需额外位")
    print(width_report())
    count, worst_m, worst_s = verify_roundtrip()
    print(f"  实证：{count} 组边界+随机样本互逆无损，"
          f"M 实测最大位宽 {worst_m} bit，S 实测最大位宽 {worst_s} bit")

    _print_header("3. 与普通浮点矩阵的差异")
    for left, right in ((INT16_MAX, INT16_MIN), (3, 2), (-3, -2)):
        print(f"样本 (L, R) = ({left}, {right})")
        for precision in ("f16", "f32", "f64"):
            print("  " + trace_float(left, right, precision).replace("\n", "\n  "))
        print("-" * 72)
    import random

    rng = random.Random(99)
    print("随机 5000 组 int16 样本上的浮点矩阵往返失败率：")
    for precision in ("f16", "f32", "f64"):
        failures = overflows = 0
        for _ in range(5000):
            left = rng.randint(INT16_MIN, INT16_MAX)
            right = rng.randint(INT16_MIN, INT16_MAX)
            rec_l, rec_r, diag = float_matrix_roundtrip(left, right, precision)
            if diag["overflow"]:
                overflows += 1
            if (rec_l, rec_r) != (left, right) or diag["overflow"]:
                failures += 1
        print(f"  {precision}: 失败 {failures:>4d}/5000"
              f"（其中溢出 inf {overflows} 次）")
    print("  对照：整数 lifting 在同样本上 0 失败，且 M/S 始终为纯整数、无指数/尾数开销")

    _print_header("4. 落盘：带位宽元信息的 MSI 文件")
    samples = make_demo_wav(wav_path)
    meta = encode_wav_to_msi(wav_path, msi_path)
    print(f"  WAV 源文件     : {wav_path}（{len(samples)} 帧立体声）")
    print(f"  MSI 变换文件   : {msi_path}（{os.path.getsize(msi_path)} 字节）")
    print("  元信息         :")
    for key in sorted(meta):
        print(f"    {key}: {meta[key]}")
    frames, meta_back = read_msi(msi_path)
    restored = decode_stereo(frames)
    assert restored == samples, "逆变换未能逐样本还原！"
    write_wav_stereo(restored_path, restored, meta_back.get("sample_rate", 48000))
    print(f"  逆变换还原 WAV : {restored_path}")
    print(f"  逐样本比对     : {len(samples)} 帧全部精确一致 ✓")

    _print_header("5. 错误位宽/损坏文件的拒绝结果")
    _demo_rejections(msi_path, meta, out_dir)


def _demo_rejections(good_msi: str, meta: dict, out_dir: str) -> None:
    good = open(good_msi, "rb").read()
    meta_json = json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8")
    payload = good[len(MAGIC) + 4 + len(meta_json):]

    def reject(name: str, data: bytes) -> None:
        path = os.path.join(out_dir, name)
        with open(path, "wb") as handle:
            handle.write(data)
        try:
            read_msi(path)
        except MSIFormatError as exc:
            print(f"  [拒绝] {name:<22} -> {exc}")
        else:
            print(f"  [异常] {name} 未被拒绝！")

    # 5a. 位宽声明错误：声明 16 位，但帧内存在 17 位值
    bad = dict(meta)
    bad["mid_width_bits"] = 16
    bad["side_width_bits"] = 16
    raw = json.dumps(bad, ensure_ascii=False, sort_keys=True).encode("utf-8")
    reject("bad_width16.msi", MAGIC + struct.pack("<I", len(raw)) + raw + payload)

    # 5b. 帧计数与载荷不符
    bad = dict(meta)
    bad["frame_count"] = meta["frame_count"] + 5
    raw = json.dumps(bad, ensure_ascii=False, sort_keys=True).encode("utf-8")
    reject("bad_count.msi", MAGIC + struct.pack("<I", len(raw)) + raw + payload)

    # 5c. 魔数错误
    reject("bad_magic.msi", b"XXXXXXXX" + good[8:])

    # 5d. 载荷被截断
    reject("truncated.msi", good[:-4])

    # 5e. 元信息缺字段
    bad = dict(meta)
    del bad["side_width_bits"]
    raw = json.dumps(bad, ensure_ascii=False, sort_keys=True).encode("utf-8")
    reject("missing_field.msi", MAGIC + struct.pack("<I", len(raw)) + raw + payload)

    # 5f. 位宽契约违约：载荷按 16 位容器重新打包，但帧内含 17 位极值，
    #     长度自洽，只有值范围校验能识别并拒绝。
    frames, _ = read_msi(good_msi)
    # 5f. 值范围违约：容器字节宽度不变（仍 3 字节/值，故长度自洽），
    #     但把一个需要 18 位的值塞进声明为 17 位的通道，
    #     长度检查发现不了，只有逐值有符号位宽校验能拒绝。
    bad = dict(meta)
    bad["mid_width_bits"] = 17
    bad["side_width_bits"] = 17
    poisoned = list(frames)
    poisoned[0] = (90000, poisoned[0][1])  # 90000 需要 18 位有符号
    wide_payload = bytearray()
    for mid, side in poisoned:
        wide_payload += mid.to_bytes(3, "little", signed=True)
        wide_payload += side.to_bytes(3, "little", signed=True)
    raw = json.dumps(bad, ensure_ascii=False, sort_keys=True).encode("utf-8")
    reject("width_contract_violation.msi",
           MAGIC + struct.pack("<I", len(raw)) + raw + bytes(wide_payload))
    # 5g. 写入侧的前置拒绝：真实 17 位变换结果不允许声明成 16 位落盘
    try:
        write_msi(os.path.join(out_dir, "should_not_exist.msi"),
                  frames, mid_width=16, side_width=16)
    except MSIFormatError as exc:
        print(f"  [写入侧拒绝] 17 位变换结果不能声明为 16 位容器：{exc}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="立体声 Mid/Side 可逆整数 lifting 教学台"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="运行完整教学演示并生成产物文件")
    demo.add_argument("--out", default="artifacts/msi_demo", help="产物目录")

    enc = sub.add_parser("encode", help="WAV -> MSI（整数 Mid/Side）")
    enc.add_argument("wav")
    enc.add_argument("msi")

    dec = sub.add_parser("decode", help="MSI -> WAV（逐样本精确逆变换）")
    dec.add_argument("msi")
    dec.add_argument("wav")

    trace = sub.add_parser("trace", help="单样本逐步推导")
    trace.add_argument("left", type=int)
    trace.add_argument("right", type=int)
    trace.add_argument("--float", choices=("f16", "f32", "f64"), default=None)

    sub.add_parser("verify", help="边界+随机互逆性验证")
    sub.add_parser("widths", help="打印位宽/溢出分析")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "demo":
        run_demo(args.out)
    elif args.command == "encode":
        meta = encode_wav_to_msi(args.wav, args.msi)
        print(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.command == "decode":
        frames, meta = read_msi(args.msi)
        samples = decode_stereo(frames)
        write_wav_stereo(args.wav, samples, meta.get("sample_rate", 48000))
        print(f"已还原 {len(samples)} 帧 -> {args.wav}")
    elif args.command == "trace":
        print(trace_forward(args.left, args.right))
        mid, side = encode_sample(args.left, args.right)
        print(trace_inverse(mid, side))
        if args.float:
            print(trace_float(args.left, args.right, args.float))
    elif args.command == "verify":
        count, worst_m, worst_s = verify_roundtrip()
        print(f"通过 {count} 组样本；M 最大位宽 {worst_m}，S 最大位宽 {worst_s}")
    elif args.command == "widths":
        print(width_report())
    return 0


if __name__ == "__main__":
    sys.exit(main())

