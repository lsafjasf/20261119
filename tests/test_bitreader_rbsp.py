import unittest

from h264_sei.bitreader import BitReader, EndOfBitsError
from h264_sei.rbsp import remove_emulation_prevention, split_annex_b
from tests.bitwriter import BitWriter, add_emulation_prevention


class BitReaderTest(unittest.TestCase):
    def test_fixed_bits_and_alignment(self):
        reader = BitReader(bytes([0b10110011, 0b11110000]))
        self.assertEqual(reader.read_bits(4), 0xB)
        self.assertEqual(reader.read_bits(3), 0b001)
        self.assertFalse(reader.is_byte_aligned())
        reader.byte_align()
        self.assertTrue(reader.is_byte_aligned())
        self.assertEqual(reader.read_bits(8), 0xF0)

    def test_exp_golomb_roundtrip(self):
        writer = BitWriter()
        for value in [0, 1, 2, 3, 7, 15, 100, 1000]:
            writer.write_ue(value)
        for signed in [0, 1, -1, 2, -2, 100, -100]:
            writer.write_se(signed)
        writer.rbsp_trailing_bits()
        reader = BitReader(writer.to_bytes())
        for value in [0, 1, 2, 3, 7, 15, 100, 1000]:
            self.assertEqual(reader.read_ue(), value)
        for signed in [0, 1, -1, 2, -2, 100, -100]:
            self.assertEqual(reader.read_se(), signed)

    def test_past_end_raises(self):
        reader = BitReader(b"\x00")
        with self.assertRaises(EndOfBitsError):
            reader.read_bits(9)


class AnnexBTest(unittest.TestCase):
    def test_three_and_four_byte_start_codes(self):
        stream = b"\x00\x00\x00\x01\x67\xaa" + b"\x00\x00\x01\x65\xbb"
        nals = split_annex_b(stream)
        self.assertEqual(nals, [b"\x67\xaa", b"\x65\xbb"])

    def test_leading_garbage_and_trailing_zeros(self):
        stream = b"junk" + b"\x00\x00\x01\x09\x00\x00"
        nals = split_annex_b(stream)
        # The final NAL reaches EOF; its trailing zero bytes are preserved so
        # a genuinely truncated stream is not silently modified.
        self.assertEqual(nals, [b"\x09\x00\x00"])

    def test_trailing_zeros_between_nals_are_padding(self):
        stream = b"\x00\x00\x01\x09\x00\x00" + b"\x00\x00\x01\x0a"
        self.assertEqual(split_annex_b(stream), [b"\x09", b"\x0a"])

    def test_empty(self):
        self.assertEqual(split_annex_b(b""), [])


class EmulationPreventionTest(unittest.TestCase):
    def test_roundtrip(self):
        for rbsp in [
            b"\x00\x00\x00\x00\x00\x01\x00\x00\x02\x00\x00\x03",
            bytes(range(256)) * 2,
            b"\x00\x00\x03\x04",
        ]:
            ebsp = add_emulation_prevention(rbsp)
            self.assertEqual(remove_emulation_prevention(ebsp), rbsp)

    def test_malformed_sequence_preserved(self):
        # 00 00 04 is not a prevention sequence and stays untouched.
        self.assertEqual(remove_emulation_prevention(b"\x00\x00\x04"), b"\x00\x00\x04")


if __name__ == "__main__":
    unittest.main()
