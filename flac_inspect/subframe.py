"""FLAC subframe decoding for structural validation."""

from .bitreader import BitReader, BitReaderError
from .model import SubframeInfo


class SubframeError(ValueError):
    """Raised when a subframe is reserved or cannot be decoded."""


def _residual(reader: BitReader, block_size: int, predictor_order: int) -> list[int]:
    method = reader.read(2)
    if method > 1:
        raise SubframeError(f"reserved residual coding method {method}")

    partition_order = reader.read(4)
    partitions = 1 << partition_order
    if block_size % partitions:
        raise SubframeError(
            f"block size {block_size} is not divisible by {partitions} partitions"
        )
    partition_samples = block_size // partitions
    if partition_samples < predictor_order and partition_order == 0:
        raise SubframeError("predictor order exceeds block size")

    parameter_bits = 4 if method == 0 else 5
    escape = (1 << parameter_bits) - 1
    result: list[int] = []

    for partition in range(partitions):
        count = partition_samples - predictor_order if partition == 0 else partition_samples
        if count < 0:
            raise SubframeError("predictor order exceeds first residual partition")
        parameter = reader.read(parameter_bits)
        if parameter == escape:
            raw_bits = reader.read(5)
            result.extend(reader.read_signed(raw_bits) for _ in range(count))
        else:
            for _ in range(count):
                quotient = reader.read_unary_zeroes()
                remainder = reader.read(parameter) if parameter else 0
                unsigned = (quotient << parameter) | remainder
                result.append((unsigned >> 1) ^ -(unsigned & 1))

    expected = block_size - predictor_order
    if len(result) != expected:
        raise SubframeError(f"decoded {len(result)} residuals, expected {expected}")
    return result


def _fixed(reader: BitReader, block_size: int, bits: int, order: int) -> list[int]:
    samples = [reader.read_signed(bits) for _ in range(order)]
    for residual in _residual(reader, block_size, order):
        if order == 0:
            predicted = 0
        elif order == 1:
            predicted = samples[-1]
        elif order == 2:
            predicted = 2 * samples[-1] - samples[-2]
        elif order == 3:
            predicted = 3 * samples[-1] - 3 * samples[-2] + samples[-3]
        else:
            predicted = (
                4 * samples[-1]
                - 6 * samples[-2]
                + 4 * samples[-3]
                - samples[-4]
            )
        samples.append(predicted + residual)
    return samples


def _lpc(reader: BitReader, block_size: int, bits: int, order: int) -> list[int]:
    samples = [reader.read_signed(bits) for _ in range(order)]
    raw_precision = reader.read(4)
    if raw_precision == 15:
        raise SubframeError("reserved LPC coefficient precision 16")
    precision = raw_precision + 1
    shift = reader.read_signed(5)
    coefficients = [reader.read_signed(precision) for _ in range(order)]

    for residual in _residual(reader, block_size, order):
        prediction = sum(
            coefficient * samples[-1 - index]
            for index, coefficient in enumerate(coefficients)
        )
        if shift >= 0:
            prediction >>= shift
        else:
            prediction <<= -shift
        samples.append(prediction + residual)
    return samples


def decode_subframe(
    reader: BitReader, channel: int, block_size: int, channel_bits: int
) -> SubframeInfo:
    zero_pad = reader.read(1)
    if zero_pad:
        raise SubframeError("subframe zero padding bit is set")

    subframe_type = reader.read(6)
    wasted_flag = reader.read(1)
    wasted_bits = reader.read_unary_zeroes(limit=channel_bits) + 1 if wasted_flag else 0
    effective_bits = channel_bits - wasted_bits
    if effective_bits <= 0:
        raise SubframeError(f"wasted bits {wasted_bits} consume all {channel_bits} bits")

    if subframe_type == 0:
        value = reader.read_signed(effective_bits)
        samples = [value] * block_size
        kind = "CONSTANT"
        detail = f"CONSTANT value={value}"
    elif subframe_type == 1:
        samples = [reader.read_signed(effective_bits) for _ in range(block_size)]
        kind = "VERBATIM"
        detail = f"VERBATIM {effective_bits}-bit samples"
    elif 8 <= subframe_type <= 12:
        order = subframe_type - 8
        samples = _fixed(reader, block_size, effective_bits, order)
        kind = "FIXED"
        detail = f"FIXED order={order}"
    elif 32 <= subframe_type <= 63:
        order = subframe_type - 31
        samples = _lpc(reader, block_size, effective_bits, order)
        kind = "LPC"
        detail = f"LPC order={order}"
    else:
        raise SubframeError(f"reserved subframe type {subframe_type}")

    if wasted_bits:
        samples = [sample << wasted_bits for sample in samples]
        detail += f" wasted_bits={wasted_bits}"

    if len(samples) != block_size:
        raise SubframeError(f"decoded {len(samples)} samples, expected {block_size}")
    return SubframeInfo(
        channel=channel,
        kind=kind,
        detail=detail,
        declared_bits=channel_bits,
        effective_bits=effective_bits,
        wasted_bits=wasted_bits,
        samples=samples,
    )


def decode_channels(
    reader: BitReader, block_size: int, bits_per_sample: int, assignment: int
) -> tuple[list[SubframeInfo], list[list[int]]]:
    if assignment <= 7:
        channel_bits = [bits_per_sample] * (assignment + 1)
    elif assignment == 8:
        channel_bits = [bits_per_sample, bits_per_sample + 1]
    elif assignment == 9:
        channel_bits = [bits_per_sample + 1, bits_per_sample]
    elif assignment == 10:
        channel_bits = [bits_per_sample, bits_per_sample + 1]
    else:
        raise SubframeError(f"reserved channel assignment {assignment}")

    try:
        subframes = [
            decode_subframe(reader, channel, block_size, channel_bits[channel])
            for channel in range(len(channel_bits))
        ]
    except BitReaderError as error:
        raise SubframeError(str(error)) from error

    encoded = [subframe.samples for subframe in subframes]
    if assignment <= 7:
        decoded = encoded
    elif assignment == 8:
        left, side = encoded
        decoded = [left, [left_value - side_value for left_value, side_value in zip(left, side)]]
    elif assignment == 9:
        side, right = encoded
        decoded = [[side_value + right_value for side_value, right_value in zip(side, right)], right]
    else:
        mid, side = encoded
        left = []
        right = []
        for mid_value, side_value in zip(mid, side):
            expanded_mid = (mid_value << 1) | (side_value & 1)
            left.append((expanded_mid + side_value) >> 1)
            right.append((expanded_mid - side_value) >> 1)
        decoded = [left, right]
    return subframes, decoded
