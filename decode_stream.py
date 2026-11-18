#!/usr/bin/env python3
"""Standalone decoder for the LPC laboratory's .coef and .res streams.

Usage:
    python3 decode_stream.py speech.coef speech.res speech.recon.wav

This file has no project imports and only needs the Python standard library.
The synthesis convention is x[n] = e[n] + sum(a[i] * x[n-i]).
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import wave
from pathlib import Path

COEF_MAGIC = b"LPCC"
RES_MAGIC = b"LPCR"
FORMAT_VERSION = 1


def read_header(path: str | Path, expected_magic: bytes) -> tuple[dict, bytes]:
    payload = Path(path).read_bytes()
    if payload[:4] != expected_magic:
        raise ValueError(f"{path}: bad magic (expected {expected_magic!r})")
    version, header_length = struct.unpack_from("<BI", payload, 4)
    if version != FORMAT_VERSION:
        raise ValueError(f"{path}: unsupported version {version}")
    offset = 9 + header_length
    if offset > len(payload):
        raise ValueError(f"{path}: truncated header")
    return json.loads(payload[9:offset]), payload[offset:]


def unpack_unsigned(payload: bytes, count: int, bits: int) -> list[int]:
    if not 1 <= bits <= 16:
        raise ValueError("packed field width must be 1..16 bits")
    values: list[int] = []
    accumulator = 0
    available = 0
    mask = (1 << bits) - 1
    for byte in payload:
        accumulator = (accumulator << 8) | byte
        available += 8
        while available >= bits and len(values) < count:
            available -= bits
            values.append((accumulator >> available) & mask)
    if len(values) != count:
        raise ValueError("truncated packed payload")
    return values


def dequantize_reflection(code: int, bits: int) -> float:
    return -1.0 + (code + 0.5) * 2.0 / (1 << bits)


def reflection_to_predictor(reflection: list[float]) -> list[float]:
    predictor = [1.0]
    for stage, coefficient in enumerate(reflection, start=1):
        previous = predictor[:]
        predictor.append(coefficient)
        for index in range(1, stage):
            predictor[index] = (
                previous[index] - coefficient * previous[stage - index]
            )
    return predictor


def dequantize_residual(
    payload: bytes, count: int, bits: int, scale: float
) -> list[float]:
    codes = unpack_unsigned(payload, count, bits)
    sign_bit = 1 << (bits - 1)
    signed = [
        code - (1 << bits) if code & sign_bit else code for code in codes
    ]
    limit = (1 << bits) // 2 - 1
    if scale == 0.0 or limit == 0:
        return [0.0] * count
    return [code / limit * scale for code in signed]


def decode(coef_path: str | Path, residual_path: str | Path) -> tuple[list[float], dict]:
    coef_header, coef_payload = read_header(coef_path, COEF_MAGIC)
    res_header, res_payload = read_header(residual_path, RES_MAGIC)
    if coef_header["sample_rate"] != res_header["sample_rate"]:
        raise ValueError("coefficient/residual sample-rate mismatch")
    if coef_header["frame_count"] != res_header["frame_count"]:
        raise ValueError("coefficient/residual frame-count mismatch")
    if coef_header["frame_length"] != res_header["frame_length"]:
        raise ValueError("coefficient/residual frame-length mismatch")
    if coef_header["residual_bits"] != res_header["residual_bits"]:
        raise ValueError("coefficient/residual bit-depth mismatch")

    order = int(coef_header["order"])
    coefficient_bits = int(coef_header["coefficient_bits"])
    mode = coef_header["coefficient_mode"]
    frame_length = int(coef_header["frame_length"])
    frame_count = int(coef_header["frame_count"])
    sample_count = int(coef_header["sample_count"])
    residual_bits = int(res_header["residual_bits"])
    coefficient_bytes = (order * coefficient_bits + 7) // 8
    residual_bytes = (frame_length * residual_bits + 7) // 8
    coefficient_record = 1 + coefficient_bytes + (4 if mode == "direct" else 0)
    residual_record = 4 + residual_bytes

    if mode not in {"rc", "direct"}:
        raise ValueError(f"unknown coefficient mode: {mode}")
    if len(coef_payload) < frame_count * coefficient_record:
        raise ValueError("truncated coefficient stream")
    if len(res_payload) < frame_count * residual_record:
        raise ValueError("truncated residual stream")

    output: list[float] = []
    coef_offset = 0
    res_offset = 0
    for _ in range(frame_count):
        _flags = coef_payload[coef_offset]
        coef_offset += 1
        if mode == "direct":
            direct_scale = struct.unpack_from("<f", coef_payload, coef_offset)[0]
            coef_offset += 4
        packed = coef_payload[coef_offset : coef_offset + coefficient_bytes]
        coef_offset += coefficient_bytes
        codes = unpack_unsigned(packed, order, coefficient_bits)
        if mode == "rc":
            reflection = [
                dequantize_reflection(code, coefficient_bits) for code in codes
            ]
            predictor = reflection_to_predictor(reflection)
        else:
            sign_bit = 1 << (coefficient_bits - 1)
            signed = [
                code - (1 << coefficient_bits) if code & sign_bit else code
                for code in codes
            ]
            limit = (1 << (coefficient_bits - 1)) - 1
            direct = (
                [code / limit * direct_scale for code in signed]
                if limit
                else [0.0] * order
            )
            predictor = [1.0] + direct

        residual_scale = struct.unpack_from("<f", res_payload, res_offset)[0]
        res_offset += 4
        residual_payload = res_payload[res_offset : res_offset + residual_bytes]
        res_offset += residual_bytes
        excitation = dequantize_residual(
            residual_payload, frame_length, residual_bits, residual_scale
        )
        frame_start = len(output)
        history = list(reversed(output[-max(order, 1) :]))
        for index, value in enumerate(excitation):
            prediction = 0.0
            for lag in range(1, order + 1):
                delayed_index = index - lag
                if delayed_index >= 0:
                    delayed = output[frame_start + delayed_index]
                elif lag - 1 < len(history):
                    delayed = history[lag - 1]
                else:
                    delayed = 0.0
                prediction += predictor[lag] * delayed
            output.append(value + prediction)

    alpha = float(coef_header.get("preemphasis", 0.0))
    if alpha > 0.0:
        emphasized = output
        output = []
        previous = 0.0
        for value in emphasized:
            current = value + alpha * previous
            output.append(current)
            previous = current
    if not all(math.isfinite(value) for value in output):
        raise ValueError("decoder produced non-finite samples")
    return output[:sample_count], coef_header


def write_wav(path: str | Path, samples: list[float], sample_rate: int) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = bytearray()
    for value in samples:
        value = float(value)
        if value >= 1.0:
            integer = 32767
        elif value <= -1.0:
            integer = -32768
        else:
            integer = int(round(value * 32767.0))
        payload += max(-32768, min(32767, integer)).to_bytes(
            2, "little", signed=True
        )
    with wave.open(str(destination), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(payload))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coefficients", help="input .coef coefficient stream")
    parser.add_argument("residual", help="input .res residual stream")
    parser.add_argument("output", help="output reconstructed WAV")
    args = parser.parse_args()
    samples, header = decode(args.coefficients, args.residual)
    write_wav(args.output, samples, int(header["sample_rate"]))
    duration = len(samples) / header["sample_rate"] if header["sample_rate"] else 0.0
    print(
        f"decoded {len(samples)} samples ({duration:.3f}s), "
        f"order={header['order']}, mode={header['coefficient_mode']}"
    )


if __name__ == "__main__":
    main()
