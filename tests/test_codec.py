import random
import unittest

from watermark_tracker.codec import (
    RS_N,
    decode_code_bits,
    decode_payload,
    encode_payload,
    rs_decode,
    rs_encode,
)


class CodecTests(unittest.TestCase):
    def test_rs_corrects_three_symbol_errors(self):
        randomizer = random.Random(1)
        for _ in range(200):
            data = [randomizer.randrange(16) for _ in range(8)]
            codeword = rs_encode(data)
            damaged = list(codeword)
            for position in randomizer.sample(range(RS_N), 3):
                damaged[position] ^= randomizer.randrange(1, 16)
            decoded = rs_decode(damaged)
            self.assertIsNotNone(decoded)
            self.assertEqual(list(decoded.data), data)
            self.assertEqual(decoded.corrected_symbols, 3)

    def test_payload_round_trip_and_bit_errors(self):
        for marker_id in (0, 7, 42, 4096, 65535):
            bits = encode_payload(marker_id)
            self.assertEqual(decode_payload(bits).marker_id, marker_id)
            damaged = list(bits)
            damaged[4] ^= 1
            damaged[17] ^= 1
            damaged[58] ^= 1
            decoded = decode_code_bits(damaged)
            self.assertIsNotNone(decoded)
            self.assertEqual(decoded.payload.marker_id, marker_id)
            self.assertEqual(decoded.corrected_symbols, 3)


if __name__ == "__main__":
    unittest.main()
