from __future__ import annotations

import random
import struct
import unittest
import zlib

from ima_adpcm.codec import (
    STEP_TABLE,
    ChannelState,
    CrcMismatch,
    InvalidBlock,
    decode_block,
    encode_block,
    parse_block_header,
)
from ima_adpcm.reference import decode_block_reference, verify_against_reference


def make_legal_block(
    channels: int,
    frames: int,
    states: list[ChannelState],
    payload: bytes,
) -> bytes:
    body = struct.pack(
        "<4sBBHHH", b"IADB", 1, channels, frames, len(payload), 0
    )
    body += b"".join(
        struct.pack("<hBB", state.predictor, state.step_index, 0)
        for state in states
    )
    body += payload
    return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


class KnownVectorTests(unittest.TestCase):
    def test_hand_calculated_low_nibble_first_vector(self) -> None:
        # Codes are 7,0,8,f.  The first code occupies the low nibble of 07.
        block = make_legal_block(1, 5, [ChannelState(0, 0)], bytes((0x07, 0xF8)))
        decoded = decode_block(block, collect_trace=True)
        self.assertEqual(decoded.interleaved(), (0, 11, 13, 12, -11))
        self.assertEqual([event.code for event in decoded.trace], [7, 0, 8, 15])
        self.assertEqual(
            [event.prediction for event in decoded.trace], [0, 11, 13, 12]
        )
        self.assertEqual(
            [event.quantized_difference for event in decoded.trace],
            [11, 2, -1, -23],
        )
        self.assertEqual(
            [
                (event.step_index_before, event.step_index_after)
                for event in decoded.trace
            ],
            [(0, 8), (8, 7), (7, 6), (6, 14)],
        )
        self.assertEqual(STEP_TABLE[0], 7)

    def test_reference_matches_known_vector(self) -> None:
        block = make_legal_block(1, 5, [ChannelState(0, 0)], bytes((0x07, 0xF8)))
        reference = decode_block_reference(block)
        self.assertEqual(
            reference.samples, ((0, 11, 13, 12, -11),)
        )
        self.assertEqual(verify_against_reference(block), 5)


class BlockFormatTests(unittest.TestCase):
    def test_header_fields_and_channel_state(self) -> None:
        frames = [[10, 20, 30], [-10, -20, -30]]
        encoded = encode_block(frames, initial_index=(3, 88))
        self.assertEqual(
            struct.unpack_from("<4sBBHHH", encoded, 0),
            (b"IADB", 1, 2, 3, 2, 0),
        )
        self.assertEqual(
            struct.unpack_from("<hBBhBB", encoded, 12),
            (10, 3, 0, -10, 88, 0),
        )
        self.assertEqual(
            struct.unpack_from("<I", encoded, len(encoded) - 4)[0],
            zlib.crc32(encoded[:-4]) & 0xFFFFFFFF,
        )
        parsed = parse_block_header(encoded)
        self.assertEqual(parsed.channels, 2)
        self.assertEqual(parsed.frame_count, 3)
        self.assertEqual(parsed.payload_size, 2)
        self.assertEqual(parsed.total_size, len(encoded))

    def test_frame_major_channel_minor_trace_order(self) -> None:
        encoded = encode_block([[0, 100, 200], [0, -100, -200]])
        decoded = decode_block(encoded, collect_trace=True)
        self.assertEqual(
            [(event.frame, event.channel) for event in decoded.trace],
            [(1, 0), (1, 1), (2, 0), (2, 1)],
        )
        self.assertEqual(decoded.samples[0][0], 0)
        self.assertEqual(decoded.samples[1][0], 0)
        self.assertEqual(decoded.interleaved()[:2], (0, 0))

    def test_header_frame_is_exact_even_at_full_scale(self) -> None:
        frames = [[-32768, 32767, 0], [32767, -32768, 1]]
        decoded = decode_block(encode_block(frames))
        self.assertEqual([channel[0] for channel in decoded.samples], [-32768, 32767])

    def test_crc_corruption_is_detected(self) -> None:
        encoded = bytearray(encode_block([[0, 100, 200, 300]]))
        header = parse_block_header(encoded)
        encoded[header.header_size] ^= 0x01
        with self.assertRaises(CrcMismatch):
            decode_block(encoded)
        # The independent decoder still allows inspection without CRC checking.
        reference = decode_block_reference(encoded, verify_crc=False)
        self.assertEqual(reference.frame_count, 4)

    def test_invalid_step_index_is_rejected(self) -> None:
        block = make_legal_block(1, 2, [ChannelState(0, 89)], b"\x00")
        with self.assertRaises(InvalidBlock):
            parse_block_header(block)


class ReferenceDifferentialTests(unittest.TestCase):
    def test_random_legal_blocks_match_independent_decoder(self) -> None:
        rng = random.Random(0xC0DEC)
        for channels in range(1, 5):
            for _ in range(20):
                frame_count = rng.randint(1, 40)
                frames = [
                    [rng.randint(-32768, 32767) for _ in range(frame_count)]
                    for _ in range(channels)
                ]
                indices = [rng.randint(0, 88) for _ in range(channels)]
                block = encode_block(frames, initial_index=indices)
                core = decode_block(block, collect_trace=True)
                reference = decode_block_reference(block)
                self.assertEqual(core.samples, reference.samples)
                self.assertEqual(
                    [
                        (
                            event.frame,
                            event.channel,
                            event.code,
                            event.prediction,
                            event.quantized_difference,
                            event.reconstructed,
                            event.step_index_before,
                            event.step_index_after,
                        )
                        for event in core.trace
                    ],
                    [
                        (
                            event.frame,
                            event.channel,
                            event.code,
                            event.predictor_before,
                            event.difference,
                            event.predictor_after,
                            event.index_before,
                            event.index_after,
                        )
                        for event in reference.steps
                    ],
                )

    def test_silence_and_index_bounds(self) -> None:
        source = [[0] * 500, [0] * 500]
        decoded = decode_block(encode_block(source))
        self.assertEqual(decoded.samples, ((0,) * 500, (0,) * 500))
        for event in decode_block(
            encode_block([[-32768, 32767, -32768, 32767]], initial_index=87),
            collect_trace=True,
        ).trace:
            self.assertGreaterEqual(event.step_index_after, 0)
            self.assertLessEqual(event.step_index_after, 88)


if __name__ == "__main__":
    unittest.main()
