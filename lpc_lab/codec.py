"""Quantized LPC encoder/decoder and compact binary stream formats.

Two coefficient quantizers are provided:

* ``rc`` (default): uniform quantization of reflection coefficients.  Codes
  stay strictly inside (-1, 1), so the synthesis filter is stable by design.
* ``direct``: quantize the predictor coefficients themselves.  Each frame is
  checked for poles outside the unit circle; unstable frames are stabilized by
  coefficient shrinking, and if that fails they are rejected to a zero
  predictor.  Every action is recorded in frame flags and statistics.
"""

from __future__ import annotations

import json
import math
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .dsp import (
    AnalysisConfig,
    all_pole_stable,
    analyze_signal,
    deemphasize,
    get_frame,
    pole_radius,
    preemphasize,
    prediction_error,
    reflection_to_predictor,
    rms,
    snr_db,
    synthesize_frame,
)
from .wavio import read_wav, write_wav

COEF_MAGIC = b"LPCC"
RES_MAGIC = b"LPCR"
FORMAT_VERSION = 1

FLAG_SILENCE = 0x01
FLAG_STABILIZED = 0x02
FLAG_REJECTED = 0x04
FLAG_UNSTABLE_SEEN = 0x08


@dataclass
class EncodedStream:
    coef_path: Path
    residual_path: Path
    reconstruction: list[float]
    analysis: dict
    reconstruction_snr_db: float
    reconstruction_mse: float
    stability: dict
    sizes: dict


def _pack_unsigned(values: Sequence[int], bits: int) -> bytes:
    if not 1 <= bits <= 16:
        raise ValueError("packed fields must use 1..16 bits")
    output = bytearray()
    accumulator = 0
    available = 0
    limit = (1 << bits) - 1
    for value in values:
        if not 0 <= value <= limit:
            raise ValueError(f"value {value} does not fit in {bits} bits")
        accumulator = (accumulator << bits) | value
        available += bits
        while available >= 8:
            available -= 8
            output.append((accumulator >> available) & 0xFF)
    if available:
        output.append((accumulator << (8 - available)) & 0xFF)
    return bytes(output)


def _unpack_unsigned(payload: bytes, count: int, bits: int) -> list[int]:
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


def _signed_code(value: int) -> int:
    return value - (1 << 16) if value & 0x8000 else value


def quantize_reflection(k: float, bits: int) -> tuple[int, float]:
    levels = 1 << bits
    position = (max(-0.999999, min(0.999999, k)) + 1.0) * 0.5
    code = int(math.floor(position * levels))
    code = max(0, min(levels - 1, code))
    value = -1.0 + (code + 0.5) * 2.0 / levels
    return code, value


def dequantize_reflection(code: int, bits: int) -> float:
    levels = 1 << bits
    return -1.0 + (code + 0.5) * 2.0 / levels


def _quantize_direct(
    predictor: Sequence[float], bits: int
) -> tuple[list[int], float, list[float], bool]:
    coefficients = [float(value) for value in predictor[1:]]
    scale = max((abs(value) for value in coefficients), default=0.0)
    unstable_seen = False
    if scale <= 1e-15:
        return [0] * len(coefficients), 0.0, [0.0] * len(coefficients), False

    limit = (1 << (bits - 1)) - 1 if bits < 16 else 32767
    signed_codes: list[int] = []
    for value in coefficients:
        code = int(round(value / scale * limit))
        code = max(-limit - 1, min(limit, code))
        signed_codes.append(code)
    dequantized = (
        [code / limit * scale for code in signed_codes]
        if limit
        else [0.0] * len(signed_codes)
    )
    codes = [code & ((1 << bits) - 1) for code in signed_codes]
    restored = [1.0] + dequantized
    if not all_pole_stable(restored):
        unstable_seen = True
    return codes, scale, restored, unstable_seen


def _stabilize_direct(
    predictor: Sequence[float], max_attempts: int = 64
) -> tuple[list[float], str, int]:
    """Shrink direct predictor coefficients until all poles are inside."""

    current = [float(value) for value in predictor]
    if all_pole_stable(current):
        return current, "none", 0
    for attempt in range(1, max_attempts + 1):
        current = [1.0] + [value * 0.95 for value in current[1:]]
        if all_pole_stable(current):
            return current, "coefficient_scaling", attempt
    return [1.0] + [0.0] * (len(predictor) - 1), "rejected_to_zero", max_attempts


def _quantize_predictor(
    predictor: Sequence[float],
    reflection: Sequence[float],
    mode: str,
    bits: int,
) -> tuple[list[int], list[float], dict]:
    if mode == "rc":
        encoded: list[int] = []
        quantized_reflection: list[float] = []
        for value in reflection:
            code, restored = quantize_reflection(value, bits)
            encoded.append(code)
            quantized_reflection.append(restored)
        quantized = reflection_to_predictor(quantized_reflection)
        radius = pole_radius(quantized)
        if radius >= 1.0 - 1e-9:
            quantized_reflection = [value * 0.999 for value in quantized_reflection]
            encoded = [
                quantize_reflection(value, bits)[0]
                for value in quantized_reflection
            ]
            quantized_reflection = [
                dequantize_reflection(code, bits) for code in encoded
            ]
            quantized = reflection_to_predictor(quantized_reflection)
            if pole_radius(quantized) >= 1.0 - 1e-9:
                encoded = [1 << (bits - 1) for _ in reflection]
                quantized = [1.0] + [0.0] * (len(predictor) - 1)
                method = "rc_rejected_to_zero"
                flags = FLAG_STABILIZED | FLAG_REJECTED
            else:
                method = "rc_shrink_numeric"
                flags = FLAG_STABILIZED
        else:
            method = "rc_inherently_stable"
            flags = 0
        return encoded, quantized, {
            "flags": flags,
            "method": method,
            "scale": None,
            "unstable_seen": False,
            "stabilization_attempts": 0,
            "original_radius": pole_radius(predictor),
            "quantized_radius": pole_radius(quantized),
        }

    if mode != "direct":
        raise ValueError("coefficient mode must be 'rc' or 'direct'")
    codes, scale, quantized, unstable_seen = _quantize_direct(predictor, bits)
    flags = FLAG_UNSTABLE_SEEN if unstable_seen else 0
    attempts = 0
    if unstable_seen:
        stabilized, method, attempts = _stabilize_direct(quantized)
        codes, scale, quantized, still_unstable = _quantize_direct(stabilized, bits)
        if still_unstable:
            quantized = [1.0] + [0.0] * (len(predictor) - 1)
            codes, scale, _, _ = _quantize_direct(quantized, bits)
            method = "rejected_to_zero"
        if method == "rejected_to_zero":
            flags |= FLAG_REJECTED | FLAG_STABILIZED
        else:
            flags |= FLAG_STABILIZED
    else:
        method = "direct_stable"
    return codes, quantized, {
        "flags": flags,
        "method": method,
        "scale": scale,
        "unstable_seen": unstable_seen,
        "stabilization_attempts": attempts,
        "original_radius": pole_radius(predictor),
        "quantized_radius": pole_radius(quantized),
    }


def _quantize_residual_frame(samples: Sequence[float], bits: int) -> tuple[bytes, float, list[float]]:
    levels = 1 << bits
    limit = levels // 2 - 1
    scale = max((abs(float(value)) for value in samples), default=0.0)
    if scale <= 1e-20:
        return _pack_unsigned([0] * len(samples), bits), 0.0, [0.0] * len(samples)
    codes: list[int] = []
    restored: list[float] = []
    mask = (1 << bits) - 1
    for sample in samples:
        code = int(round(float(sample) / scale * limit))
        code = max(-levels // 2, min(limit, code))
        codes.append(code & mask)
        restored.append(code / limit * scale)
    return _pack_unsigned(codes, bits), scale, restored


def _dequantize_residual_frame(
    payload: bytes, sample_count: int, bits: int, scale: float
) -> list[float]:
    codes = _unpack_unsigned(payload, sample_count, bits)
    if bits == 16:
        signed = [_signed_code(code) for code in codes]
    else:
        signed = [
            code - (1 << bits) if code & (1 << (bits - 1)) else code
            for code in codes
        ]
    limit = (1 << bits) // 2 - 1
    if scale == 0.0 or limit == 0:
        return [0.0] * sample_count
    return [code / limit * scale for code in signed]


def _write_header(handle, magic: bytes, header: dict) -> None:
    encoded = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    handle.write(magic)
    handle.write(struct.pack("<BI", FORMAT_VERSION, len(encoded)))
    handle.write(encoded)


def _read_header(path: str | Path, magic: bytes) -> tuple[dict, bytes]:
    payload = Path(path).read_bytes()
    if payload[:4] != magic:
        raise ValueError(f"bad stream magic in {path}")
    version, header_length = struct.unpack_from("<BI", payload, 4)
    if version != FORMAT_VERSION:
        raise ValueError(f"unsupported stream version {version}")
    offset = 9 + header_length
    return json.loads(payload[9:offset]), payload[offset:]


def _frame_metrics(reference: Sequence[float], estimate: Sequence[float]) -> dict:
    count = min(len(reference), len(estimate))
    error = [reference[i] - estimate[i] for i in range(count)]
    mse = (
        sum(value * value for value in error) / count if count else 0.0
    )
    return {
        "snr_db": snr_db(reference, estimate),
        "mse": mse,
        "max_abs_error": max((abs(value) for value in error), default=0.0),
        "error_rms": rms(error),
    }


def decode_streams(
    coef_path: str | Path,
    residual_path: str | Path,
    output_length: int | None = None,
) -> tuple[list[float], dict]:
    coef_header, coef_payload = _read_header(coef_path, COEF_MAGIC)
    res_header, res_payload = _read_header(residual_path, RES_MAGIC)
    order = coef_header["order"]
    bits = coef_header["coefficient_bits"]
    mode = coef_header["coefficient_mode"]
    sample_count = coef_header["sample_count"]
    frame_length = coef_header["frame_length"]
    frame_total = coef_header["frame_count"]
    coefficient_bytes = (order * bits + 7) // 8
    direct_prefix = 4 if mode == "direct" else 0
    coef_record = 1 + direct_prefix + coefficient_bytes
    residual_bits = res_header["residual_bits"]
    residual_bytes = (frame_length * residual_bits + 7) // 8
    res_record = 4 + residual_bytes
    if len(coef_payload) < frame_total * coef_record:
        raise ValueError("truncated coefficient stream")
    if len(res_payload) < frame_total * res_record:
        raise ValueError("truncated residual stream")

    reconstructed: list[float] = []
    coef_offset = 0
    res_offset = 0
    flags_seen = {"silence": 0, "stabilized": 0, "rejected": 0, "unstable_seen": 0}
    for frame_index in range(frame_total):
        flags = coef_payload[coef_offset]
        coef_offset += 1
        scale = 1.0
        if mode == "direct":
            scale = struct.unpack_from("<f", coef_payload, coef_offset)[0]
            coef_offset += 4
        packed_coefficients = coef_payload[
            coef_offset : coef_offset + coefficient_bytes
        ]
        coef_offset += coefficient_bytes
        if mode == "rc":
            codes = _unpack_unsigned(packed_coefficients, order, bits)
            reflection = [dequantize_reflection(code, bits) for code in codes]
            predictor = reflection_to_predictor(reflection)
        else:
            codes = _unpack_unsigned(packed_coefficients, order, bits)
            limit = (1 << (bits - 1)) - 1
            signed = [
                code - (1 << bits) if code & (1 << (bits - 1)) else code
                for code in codes
            ]
            direct = [code / limit * scale for code in signed] if limit else [0.0] * order
            predictor = [1.0] + direct

        residual_scale = struct.unpack_from("<f", res_payload, res_offset)[0]
        res_offset += 4
        residual_payload = res_payload[res_offset : res_offset + residual_bytes]
        res_offset += residual_bytes
        residual = _dequantize_residual_frame(
            residual_payload, frame_length, residual_bits, residual_scale
        )
        history = list(reversed(reconstructed[-max(order, 1) :]))
        frame = synthesize_frame(residual, predictor, history)
        reconstructed.extend(frame)
        if flags & FLAG_SILENCE:
            flags_seen["silence"] += 1
        if flags & FLAG_STABILIZED:
            flags_seen["stabilized"] += 1
        if flags & FLAG_REJECTED:
            flags_seen["rejected"] += 1
        if flags & FLAG_UNSTABLE_SEEN:
            flags_seen["unstable_seen"] += 1

    if coef_header["preemphasis"] > 0.0:
        reconstructed = deemphasize(reconstructed, coef_header["preemphasis"])
    limit = sample_count if output_length is None else output_length
    metadata = {
        "coefficient_mode": mode,
        "order": order,
        "coefficient_bits": bits,
        "residual_bits": residual_bits,
        "frame_count": frame_total,
        "flags": flags_seen,
        "coef_header": coef_header,
        "res_header": res_header,
    }
    return [float(value) for value in reconstructed[:limit]], metadata


def encode_file(
    input_path: str | Path,
    output_prefix: str | Path,
    config: AnalysisConfig,
    coefficient_mode: str = "rc",
    coefficient_bits: int = 8,
    residual_bits: int = 8,
) -> EncodedStream:
    """Analyze, quantize, decode and write all stream artifacts."""

    config.validate()
    if not 1 <= coefficient_bits <= 16:
        raise ValueError("coefficient_bits must be in 1..16")
    if not 4 <= residual_bits <= 16:
        raise ValueError("residual_bits must be in 4..16")
    prefix = Path(output_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    samples, sample_rate = read_wav(input_path)
    if config.sample_rate != sample_rate:
        raise ValueError(
            f"sample-rate mismatch: stream uses {config.sample_rate}, file has {sample_rate}"
        )
    analysis = analyze_signal(samples, config)
    total_frames = analysis["frame_count"]
    coef_path = prefix.with_suffix(".coef")
    residual_path = prefix.with_suffix(".res")
    reconstructed_path = prefix.with_suffix(".recon.wav")
    coef_header = {
        "sample_rate": sample_rate,
        "sample_count": len(samples),
        "frame_length": config.frame_length,
        "hop_length": config.hop_length,
        "window_length": config.window_length,
        "window_type": config.window_type,
        "preemphasis": config.preemphasis,
        "order": config.order,
        "frame_count": total_frames,
        "coefficient_mode": coefficient_mode,
        "coefficient_bits": coefficient_bits,
        "residual_bits": residual_bits,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    res_header = {
        "sample_rate": sample_rate,
        "sample_count": len(samples),
        "frame_length": config.frame_length,
        "hop_length": config.hop_length,
        "frame_count": total_frames,
        "residual_bits": residual_bits,
    }

    stability = {
        "mode": coefficient_mode,
        "frames": total_frames,
        "silence_frames": 0,
        "unstable_detected": 0,
        "stabilized": 0,
        "rejected": 0,
        "max_quantized_pole_radius": 0.0,
        "max_nan_or_inf": 0,
        "methods": {},
    }
    selected_residual: list[float] = []
    working = preemphasize(samples, config.preemphasis)
    reconstructed_working: list[float] = []
    with coef_path.open("wb") as coef_handle, residual_path.open("wb") as res_handle:
        _write_header(coef_handle, COEF_MAGIC, coef_header)
        _write_header(res_handle, RES_MAGIC, res_header)
        for index, frame_record in enumerate(analysis["frames"]):
            codes, quantized, info = _quantize_predictor(
                frame_record["predictor"],
                frame_record["reflection"],
                coefficient_mode,
                coefficient_bits,
            )
            flags = info["flags"]
            if frame_record["is_silence"]:
                flags |= FLAG_SILENCE
                stability["silence_frames"] += 1
            if info["unstable_seen"]:
                stability["unstable_detected"] += 1
            if flags & FLAG_STABILIZED:
                stability["stabilized"] += 1
            if flags & FLAG_REJECTED:
                stability["rejected"] += 1
            radius = info["quantized_radius"]
            stability["max_quantized_pole_radius"] = max(
                stability["max_quantized_pole_radius"], radius
            )
            stability["methods"][info["method"]] = (
                stability["methods"].get(info["method"], 0) + 1
            )
            coef_handle.write(bytes([flags]))
            if coefficient_mode == "direct":
                coef_handle.write(struct.pack("<f", info["scale"]))
            coef_handle.write(_pack_unsigned(codes, coefficient_bits))
            if not all(math.isfinite(value) for value in quantized):
                stability["max_nan_or_inf"] += 1
            frame, start = get_frame(
                working, index, config.frame_length, config.hop_length
            )
            history = list(reversed(reconstructed_working[-config.order :]))
            residual_frame = prediction_error(frame, quantized, history)
            residual_payload, res_scale, restored_residual = _quantize_residual_frame(
                residual_frame, residual_bits
            )
            res_handle.write(struct.pack("<f", res_scale))
            res_handle.write(residual_payload)
            selected_residual.extend(restored_residual)
            reconstructed_working.extend(
                synthesize_frame(restored_residual, quantized, history)
            )

    reconstruction, metadata = decode_streams(coef_path, residual_path)
    reconstruction = reconstruction[: len(samples)]
    if len(reconstruction) < len(samples):
        reconstruction.extend([0.0] * (len(samples) - len(reconstruction)))
    metrics = _frame_metrics(samples, reconstruction)
    write_wav(reconstructed_path, reconstruction, sample_rate)
    write_wav(prefix.with_suffix(".residual.wav"), selected_residual[: len(samples)], sample_rate)

    coefficient_bytes = coef_path.stat().st_size
    residual_bytes = residual_path.stat().st_size
    duration = len(samples) / sample_rate if sample_rate else 0.0
    total_bytes = coefficient_bytes + residual_bytes
    sizes = {
        "coefficient_file_bytes": coefficient_bytes,
        "residual_file_bytes": residual_bytes,
        "total_bytes": total_bytes,
        "coefficient_bits_per_second": (
            coefficient_bytes * 8.0 / duration if duration else 0.0
        ),
        "residual_bits_per_second": (
            residual_bytes * 8.0 / duration if duration else 0.0
        ),
        "total_bits_per_second": total_bytes * 8.0 / duration if duration else 0.0,
        "total_kilobits_per_second": (
            total_bytes * 8.0 / duration / 1000.0 if duration else 0.0
        ),
        "compression_ratio": (
            (len(samples) * 2.0) / total_bytes if total_bytes else 0.0
        ),
    }
    return EncodedStream(
        coef_path=coef_path,
        residual_path=residual_path,
        reconstruction=reconstruction,
        analysis=analysis,
        reconstruction_snr_db=metrics["snr_db"],
        reconstruction_mse=metrics["mse"],
        stability=stability,
        sizes=sizes,
    )
