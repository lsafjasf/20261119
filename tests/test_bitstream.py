import unittest

from hevc_tiles.bitstream import (
    BitReader,
    ebsp_to_rbsp,
    ebsp_to_rbsp_index_map,
    rbsp_offset_to_ebsp,
    rbsp_to_ebsp,
)


class BitstreamTests(unittest.TestCase):
    def test_exp_golomb(self):
        reader = BitReader(bytes.fromhex("a640"))
        self.assertEqual(
            [reader.read_ue() for _ in range(4)],
            [0, 1, 2, 3],
        )

    def test_signed_exp_golomb(self):
        reader = BitReader(bytes.fromhex("a640"))
        self.assertEqual(
            [reader.read_se() for _ in range(4)],
            [0, 1, -1, 2],
        )

    def test_emulation_prevention_round_trip(self):
        raw = b"\x00\x00\x00\x00\x01\x02\x03\xff\x00\x00\x03"
        encoded = rbsp_to_ebsp(raw)
        self.assertEqual(ebsp_to_rbsp(encoded), raw)

    def test_ebsp_index_mapping(self):
        ebsp = b"\x00\x00\x03\x01\xaa"
        mapping = ebsp_to_rbsp_index_map(ebsp)
        self.assertEqual(mapping, [0, 1, 2, 2, 3, 4])
        self.assertEqual(rbsp_offset_to_ebsp(ebsp, 2), 3)
        self.assertEqual(rbsp_offset_to_ebsp(ebsp, 3), 4)
        self.assertEqual(ebsp_to_rbsp(ebsp), b"\x00\x00\x01\xaa")


if __name__ == "__main__":
    unittest.main()
