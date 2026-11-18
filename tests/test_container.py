from __future__ import annotations

import math
import struct
import unittest
import zlib

from ima_adpcm.codec import (
    CrcMismatch,
    block_offset,
    decode_block,
    decode_block_at,
    decode_file,
    encode_block,
    encode_file,
    parse_block_header,
    parse_file_header,
)
from ima_adpcm.metrics import compare_samples
from ima_adpcm.wav_io import (
    read_raw_pcm16,
    read_wav,
    write_raw_pcm16,
    write_wav_pcm16,
)


def ramp(count: int, low: int = -20000, high: int = 20000) -> list[int]:
    if count == 1:
        return [low]
    return [
        round(low + (high - low) * index / (count - 1))
        for index in range(count)
    ]


class ContainerTests(unittest.TestCase):
    def test_random_access_matches_sequential_decode(self) -> None:
        source = [ramp(1000), [-value // 2 for value in ramp(1000)]]
        encoded = encode_file(source, sample_rate=16000, frames_per_block=137)
        header = parse_file_header(encoded)
        full = decode_file(encoded)
        self.assertEqual(header.block_count, 8)
        for block_index in range(header.block_count):
            random = decode_block_at(encoded, block_index)
            first = block_index * header.frames_per_block
            last = min(header.total_frames, first + header.frames_per_block)
            self.assertEqual(
                random.samples,
                tuple(channel[first:last] for channel in full.frames),
            )
            self.assertEqual(
                block_offset(header, block_index),
                header.header_size + block_index * header.full_block_size,
            )

    def test_corrupt_block_does_not_pollute_next_block(self) -> None:
        frames_per_block = 64
        source = [ramp(frames_per_block * 4)]
        encoded = encode_file(
            source, sample_rate=8000, frames_per_block=frames_per_block
        )
        file_header = parse_file_header(encoded)
        block2_before = decode_block_at(encoded, 2, verify_crc=True)

        damaged = bytearray(encoded)
        block1_offset = block_offset(file_header, 1)
        block1 = parse_block_header(damaged, offset=block1_offset)
        damaged[block1_offset + block1.header_size] ^= 0x5A
        block2_after = decode_block_at(bytes(damaged), 2, verify_crc=True)
        self.assertEqual(block2_before.samples, block2_after.samples)

        with self.assertRaises(CrcMismatch):
            decode_file(bytes(damaged), strict=True)
        salvaged = decode_file(bytes(damaged), strict=False)
        self.assertEqual(len(salvaged.failures), 1)
        self.assertEqual(salvaged.failures[0].block_index, 1)
        self.assertEqual(salvaged.frames[0][64:128], (0,) * 64)
        self.assertEqual(
            salvaged.frames[0][128:192], block2_after.samples[0]
        )

    def test_odd_tail_is_truncated_by_header_length(self) -> None:
        source = [ramp(257)]
        encoded = encode_file(source, sample_rate=8000, frames_per_block=64)
        file_header = parse_file_header(encoded)
        final_block = decode_block_at(encoded, file_header.block_count - 1)
        decoded = decode_file(encoded)
        self.assertEqual(final_block.frame_count, 1)
        self.assertEqual(decoded.frame_count, 257)
        self.assertEqual(len(decoded.interleaved()), 257)

    def test_nonzero_padding_nibble_is_ignored(self) -> None:
        encoded = bytearray(encode_block([[0, 100, 200, 300]]))
        header = parse_block_header(encoded)
        payload_offset = header.header_size
        self.assertEqual(header.payload_size, 2)
        encoded[payload_offset + header.payload_size - 1] |= 0xF0
        crc_offset = payload_offset + header.payload_size
        struct.pack_into(
            "<I", encoded, crc_offset, zlib.crc32(encoded[:crc_offset])
        )
        decoded = decode_block(bytes(encoded))
        self.assertEqual(decoded.frame_count, 4)
        self.assertEqual(len(decoded.interleaved()), 4)

    def test_empty_audio_retains_channel_metadata(self) -> None:
        encoded = encode_file([], sample_rate=22050, channels=2)
        header = parse_file_header(encoded)
        decoded = decode_file(encoded)
        self.assertEqual(header.block_count, 0)
        self.assertEqual(decoded.channels, 2)
        self.assertEqual(decoded.frame_count, 0)
        self.assertEqual(decoded.frames, ((), ()))


class MetricsTests(unittest.TestCase):
    def test_silence_is_exact(self) -> None:
        source = [0] * 256
        encoded = encode_file([source], sample_rate=8000)
        decoded = decode_file(encoded)
        metrics = compare_samples(source, decoded.frames[0])
        self.assertEqual(metrics.max_absolute_error, 0)
        self.assertEqual(metrics.rmse, 0.0)
        self.assertTrue(math.isinf(metrics.snr_db))


class WavAndRawTests(unittest.TestCase):
    def test_wav_pcm16_roundtrip(self) -> None:
        frames = ((0, 1, -1, 32767, -32768), (5, -5, 100, -100, 0))
        wav = write_wav_pcm16(48000, 2, frames)
        parsed = read_wav(wav)
        self.assertEqual(parsed.sample_rate, 48000)
        self.assertEqual(parsed.channels, 2)
        self.assertEqual(parsed.bits_per_sample, 16)
        self.assertEqual(parsed.frames, frames)
        self.assertEqual(
            parsed.interleaved(),
            (0, 5, 1, -5, -1, 100, 32767, -100, -32768, 0),
        )

    def test_raw_pcm16_drops_partial_frame(self) -> None:
        raw = struct.pack("<hhh", 1, 2, 3)
        frames = read_raw_pcm16(raw, channels=2)
        self.assertEqual(frames, ((1,), (2,)))
        self.assertEqual(write_raw_pcm16(frames), struct.pack("<hh", 1, 2))


if __name__ == "__main__":
    unittest.main()
