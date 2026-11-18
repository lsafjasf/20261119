"""Error-corrected marker payloads using a shortened RS(15, 8) code over GF(16)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

GF_PRIMITIVE = 0x13
RS_N = 15
RS_K = 8
RS_PARITY = RS_N - RS_K
CODE_BITS = 64
PAYLOAD_VERSION = 1
PAYLOAD_MAGIC = 0x5A
ORIENTATION_TAG = 0xA


def _build_gf16() -> tuple[list[int], list[int]]:
    exponent = [0] * 30
    logarithm = [0] * 16
    value = 1
    for power in range(15):
        exponent[power] = value
        logarithm[value] = power
        value <<= 1
        if value & 0x10:
            value ^= GF_PRIMITIVE
    for power in range(15, 30):
        exponent[power] = exponent[power - 15]
    return exponent, logarithm


GF_EXP, GF_LOG = _build_gf16()


def gf_mul(left: int, right: int) -> int:
    if left == 0 or right == 0:
        return 0
    return GF_EXP[GF_LOG[left] + GF_LOG[right]]


def gf_div(numerator: int, denominator: int) -> int:
    if denominator == 0:
        raise ZeroDivisionError("GF(16) division by zero")
    if numerator == 0:
        return 0
    return GF_EXP[(GF_LOG[numerator] - GF_LOG[denominator]) % 15]


def gf_pow(base: int, power: int) -> int:
    if power == 0:
        return 1
    if base == 0:
        return 0
    return GF_EXP[(GF_LOG[base] * power) % 15]


def gf_inverse(value: int) -> int:
    if value == 0:
        raise ZeroDivisionError("GF(16) inverse of zero")
    return GF_EXP[(15 - GF_LOG[value]) % 15]


def _poly_mul(left: Sequence[int], right: Sequence[int]) -> list[int]:
    result = [0] * (len(left) + len(right) - 1)
    for left_index, left_value in enumerate(left):
        for right_index, right_value in enumerate(right):
            result[left_index + right_index] ^= gf_mul(left_value, right_value)
    return result


def _poly_eval(coefficients: Sequence[int], value: int) -> int:
    result = 0
    for coefficient in coefficients:
        result = gf_mul(result, value) ^ coefficient
    return result


def _poly_eval_ascending(coefficients: Sequence[int], value: int) -> int:
    result = 0
    power = 1
    for coefficient in coefficients:
        result ^= gf_mul(coefficient, power)
        power = gf_mul(power, value)
    return result


def _generator_polynomial() -> list[int]:
    generator = [1]
    for power in range(RS_PARITY):
        generator = _poly_mul(generator, [1, GF_EXP[power]])
    return generator


GENERATOR = _generator_polynomial()


def rs_encode(data: Sequence[int]) -> list[int]:
    if len(data) != RS_K:
        raise ValueError(f"RS(15, 8) needs exactly {RS_K} data symbols")
    if any(symbol < 0 or symbol > 15 for symbol in data):
        raise ValueError("GF(16) symbols must be in 0..15")
    working = list(data) + [0] * RS_PARITY
    for index in range(RS_K):
        coefficient = working[index]
        if coefficient == 0:
            continue
        for offset, generator_value in enumerate(GENERATOR):
            working[index + offset] ^= gf_mul(generator_value, coefficient)
    return list(data) + working[RS_K:]


@dataclass(frozen=True)
class RSDecodeResult:
    data: tuple[int, ...]
    corrected_symbols: int
    syndromes: tuple[int, ...]


def _solve_gf(matrix: list[list[int]], values: list[int]) -> list[int] | None:
    size = len(values)
    augmented = [row[:] + [value] for row, value in zip(matrix, values)]
    for column in range(size):
        pivot = next(
            (row for row in range(column, size) if augmented[row][column] != 0),
            None,
        )
        if pivot is None:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = gf_inverse(augmented[column][column])
        for index in range(column, size + 1):
            augmented[column][index] = gf_mul(augmented[column][index], divisor)
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor == 0:
                continue
            for index in range(column, size + 1):
                augmented[row][index] ^= gf_mul(
                    factor, augmented[column][index]
                )
    return [augmented[index][size] for index in range(size)]


def _berlekamp_massey(syndromes: Sequence[int]) -> list[int]:
    locator = [1]
    previous = [1]
    degree = 0
    shift = 1
    previous_discrepancy = 1
    for index, syndrome in enumerate(syndromes):
        discrepancy = syndrome
        for offset in range(1, degree + 1):
            if offset < len(locator):
                discrepancy ^= gf_mul(locator[offset], syndromes[index - offset])
        if discrepancy == 0:
            shift += 1
            continue
        saved = locator[:]
        scale = gf_div(discrepancy, previous_discrepancy)
        required = len(previous) + shift
        if len(locator) < required:
            locator.extend([0] * (required - len(locator)))
        for offset, coefficient in enumerate(previous):
            locator[offset + shift] ^= gf_mul(scale, coefficient)
        if 2 * degree <= index:
            degree = index + 1 - degree
            previous = saved
            previous_discrepancy = discrepancy
            shift = 1
        else:
            shift += 1
    return locator[: degree + 1]


def rs_decode(codeword: Sequence[int]) -> RSDecodeResult | None:
    if len(codeword) != RS_N:
        raise ValueError(f"RS(15, 8) needs exactly {RS_N} symbols")
    if any(symbol < 0 or symbol > 15 for symbol in codeword):
        raise ValueError("GF(16) symbols must be in 0..15")
    syndromes = [
        _poly_eval(codeword, GF_EXP[power]) for power in range(RS_PARITY)
    ]
    if not any(syndromes):
        return RSDecodeResult(tuple(codeword[:RS_K]), 0, tuple(syndromes))
    locator = _berlekamp_massey(syndromes)
    degree = len(locator) - 1
    if degree <= 0 or degree > RS_PARITY // 2:
        return None
    positions: list[int] = []
    error_locators: list[int] = []
    for index in range(RS_N):
        exponent = (RS_N - 1 - index) % 15
        root = gf_pow(GF_EXP[1], (15 - exponent) % 15)
        if _poly_eval_ascending(locator, root) == 0:
            positions.append(index)
            error_locators.append(gf_pow(GF_EXP[1], exponent))
    if len(positions) != degree:
        return None
    matrix: list[list[int]] = []
    for row in range(degree):
        matrix.append([gf_pow(value, row) for value in error_locators])
    magnitudes = _solve_gf(matrix, list(syndromes[:degree]))
    if magnitudes is None:
        return None
    corrected = list(codeword)
    for index, magnitude in zip(positions, magnitudes):
        corrected[index] ^= magnitude
    verify = [
        _poly_eval(corrected, GF_EXP[power]) for power in range(RS_PARITY)
    ]
    if any(verify):
        return None
    return RSDecodeResult(tuple(corrected[:RS_K]), degree, tuple(syndromes))


def crc8(payload: Sequence[int]) -> int:
    value = 0
    for byte in payload:
        value ^= byte
        for _ in range(8):
            value = ((value << 1) ^ 0x07) & 0xFF if value & 0x80 else (value << 1) & 0xFF
    return value


@dataclass(frozen=True)
class MarkerPayload:
    marker_id: int
    version: int = PAYLOAD_VERSION
    checksum: int = 0


@dataclass(frozen=True)
class PayloadDecode:
    payload: MarkerPayload
    corrected_symbols: int

    @property
    def confidence(self) -> float:
        correction_penalty = self.corrected_symbols / (RS_PARITY // 2 + 1)
        return max(0.0, min(1.0, 1.0 - correction_penalty))


def _validate_marker_id(marker_id: int) -> int:
    if not 0 <= marker_id <= 0xFFFF:
        raise ValueError("marker_id must fit in 16 bits")
    return marker_id


def _payload_data_symbols(marker_id: int) -> list[int]:
    marker_id = _validate_marker_id(marker_id)
    identifier = [(marker_id >> 12) & 0xF, (marker_id >> 8) & 0xF,
                  (marker_id >> 4) & 0xF, marker_id & 0xF]
    checksum = crc8(
        [(marker_id >> 8) & 0xFF, marker_id & 0xFF, PAYLOAD_VERSION, PAYLOAD_MAGIC]
    )
    return identifier + [
        checksum >> 4,
        checksum & 0xF,
        PAYLOAD_VERSION,
        PAYLOAD_MAGIC >> 4,
    ]


def _symbols_to_bits(symbols: Sequence[int]) -> list[int]:
    bits: list[int] = []
    for symbol in symbols:
        bits.extend((symbol >> shift) & 1 for shift in (3, 2, 1, 0))
    return bits


def _bits_to_symbols(bits: Sequence[int]) -> list[int]:
    symbols: list[int] = []
    for offset in range(0, len(bits), 4):
        value = 0
        for bit in bits[offset : offset + 4]:
            value = (value << 1) | bit
        symbols.append(value)
    return symbols


def encode_payload(marker_id: int) -> tuple[int, ...]:
    """Encode a 16-bit marker ID into 64 row-major data-grid bits."""
    codeword = rs_encode(_payload_data_symbols(marker_id))
    return tuple(_symbols_to_bits(codeword) + _symbols_to_bits([ORIENTATION_TAG]))


def decode_payload(bits: Sequence[int]) -> MarkerPayload | None:
    result = decode_code_bits(bits)
    return result.payload if result else None


def decode_code_bits(bits: Sequence[int]) -> PayloadDecode | None:
    """Decode 64 bits, correcting up to three erroneous GF(16) symbols."""
    if len(bits) != CODE_BITS:
        raise ValueError(f"marker payload must contain {CODE_BITS} bits")
    symbols = _bits_to_symbols(bits[: RS_N * 4])
    tag = _bits_to_symbols(bits[RS_N * 4 :])[0]
    if tag != ORIENTATION_TAG:
        return None
    decoded = rs_decode(symbols)
    if decoded is None:
        return None
    data = decoded.data
    marker_id = (data[0] << 12) | (data[1] << 8) | (data[2] << 4) | data[3]
    checksum = (data[4] << 4) | data[5]
    version = data[6]
    magic = (data[7] << 4) | ORIENTATION_TAG
    if version != PAYLOAD_VERSION or magic != PAYLOAD_MAGIC:
        return None
    expected = crc8(
        [(marker_id >> 8) & 0xFF, marker_id & 0xFF, version, magic]
    )
    if checksum != expected:
        return None
    return PayloadDecode(
        MarkerPayload(marker_id, version, checksum), decoded.corrected_symbols
    )


def bits_to_matrix(bits: Sequence[int], size: int = 8) -> tuple[tuple[int, ...], ...]:
    if len(bits) != size * size:
        raise ValueError("bit count does not match matrix dimensions")
    return tuple(
        tuple(bits[row * size : (row + 1) * size]) for row in range(size)
    )


def rotate_bits_clockwise(bits: Sequence[int], size: int = 8, turns: int = 1) -> tuple[int, ...]:
    matrix = [list(row) for row in bits_to_matrix(bits, size)]
    for _ in range(turns % 4):
        matrix = [
            [matrix[size - 1 - column][row] for column in range(size)]
            for row in range(size)
        ]
    return tuple(value for row in matrix for value in row)
