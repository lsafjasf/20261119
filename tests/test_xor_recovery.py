"""单元测试：块格式、XOR 恢复、故障区分与拒绝静音补齐。"""

import os
import shutil
import tempfile
import unittest

from xor_recovery import damage, pcm
from xor_recovery.blocks import (
    HEADER_SIZE,
    PARITY_SLOT,
    build_block,
    digest_payload,
    parse_block,
    parity_of,
    xor_bytes,
)
from xor_recovery.cli import main as cli_main
from xor_recovery.recover import recover_bundle
from xor_recovery.store import block_path, load_manifest, scan_block, write_bundle
from xor_recovery.verify import verify_bundle, write_recovered


class BlockFormatTests(unittest.TestCase):
    def test_build_parse_roundtrip(self):
        blob = build_block(2, 1, b"hello-block")
        parsed = parse_block(blob)
        self.assertTrue(parsed.valid)
        self.assertEqual((parsed.group, parsed.slot, parsed.payload),
                         (2, 1, b"hello-block"))
        self.assertEqual(len(blob), HEADER_SIZE + len(b"hello-block"))

    def test_payload_tamper_is_detected(self):
        blob = bytearray(build_block(0, 0, b"abcdef"))
        blob[-1] ^= 0x01
        parsed = parse_block(bytes(blob))
        self.assertFalse(parsed.valid)
        self.assertIn("摘要", parsed.reason)

    def test_header_len_tamper_is_detected(self):
        blob = bytearray(build_block(0, 0, b"abcdef"))
        blob[12] = (blob[12] + 1) & 0xFF
        parsed = parse_block(bytes(blob))
        self.assertFalse(parsed.valid)

    def test_truncation_is_detected(self):
        blob = build_block(0, 0, b"abcdef")
        parsed = parse_block(blob[:-2])
        self.assertFalse(parsed.valid)


class XorMathTests(unittest.TestCase):
    def test_xor_self_inverse(self):
        data = [os.urandom(64) for _ in range(4)]
        parity = parity_of(data)
        for index in range(4):
            acc = parity
            for j, piece in enumerate(data):
                if j != index:
                    acc = xor_bytes(acc, piece)
            self.assertEqual(acc, data[index])

    def test_unequal_length_rejected(self):
        with self.assertRaises(ValueError):
            xor_bytes(b"12", b"123")


class BundleTestBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="xr-test-")
        self.pcm_data = pcm.generate_pcm(seconds=0.4)
        write_bundle(self.root, self.pcm_data, block_size=128,
                     sample_rate=8000)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)


class BundleTests(BundleTestBase):
    def test_initial_state_all_ok_and_explicit_padding(self):
        manifest = load_manifest(self.root)
        self.assertTrue(manifest["padded_len"] >= len(self.pcm_data))
        rows = [scan_block(self.root, 0, slot) for slot in range(5)]
        self.assertTrue(all(row["state"] == "ok" for row in rows))

    def test_manifest_digest_matches_block(self):
        manifest = load_manifest(self.root)
        parsed = scan_block(self.root, 0, 2)["parsed"]
        self.assertEqual(
            parsed.digest.hex(), manifest["block_digests"]["0:2"]
        )


class RecoveryTests(BundleTestBase):
    def test_single_missing_data_block(self):
        damage.delete_block(self.root, 0, 2)
        report = recover_bundle(self.root)
        self.assertTrue(report.ok)
        self.assertEqual(report.repaired_slots, ["0:2"])
        result = verify_bundle(self.root)
        self.assertTrue(result.ok, result.message)

    def test_single_corrupt_data_block_is_distinct_from_missing(self):
        damage.flip_byte(self.root, 0, 1, 0)
        self.assertEqual(scan_block(self.root, 0, 1)["state"], "corrupt")
        report = recover_bundle(self.root)
        self.assertTrue(report.ok)
        self.assertEqual(report.corrupt_slots, ["0:1"])
        self.assertEqual(report.missing_slots, [])
        self.assertIn("已有块损坏", report.groups[0]["basis"][0])

    def test_double_missing_is_unrecoverable_with_gaps(self):
        damage.delete_block(self.root, 0, 0)
        damage.delete_block(self.root, 0, 3)
        report = recover_bundle(self.root)
        self.assertFalse(report.ok)
        self.assertEqual(report.groups[0]["status"], "unrecoverable")
        self.assertEqual(len(report.unrecovered_gaps), 2)
        self.assertTrue(os.path.exists(block_path(self.root, 0, 1)))
        with self.assertRaises(RuntimeError):
            write_recovered(self.root)

    def test_missing_plus_corrupt_is_unrecoverable(self):
        damage.delete_block(self.root, 0, 2)
        damage.flip_byte(self.root, 0, PARITY_SLOT, 0)
        report = recover_bundle(self.root)
        self.assertFalse(report.ok)
        self.assertEqual(len(report.unrecovered_gaps), 2)

    def test_corrupt_parity_is_rebuilt_from_data(self):
        damage.flip_byte(self.root, 0, PARITY_SLOT, 0)
        report = recover_bundle(self.root)
        self.assertTrue(report.ok)
        self.assertEqual(report.repaired_slots, [f"0:{PARITY_SLOT}"])
        self.assertTrue(verify_bundle(self.root).ok)

    def test_two_bad_blocks_in_different_groups_both_recover(self):
        damage.delete_block(self.root, 0, 0)
        damage.flip_byte(self.root, 1, 3, 7)
        report = recover_bundle(self.root)
        self.assertTrue(report.ok)
        self.assertEqual(sorted(report.repaired_slots), ["0:0", "1:3"])
        self.assertTrue(verify_bundle(self.root).ok)

    def test_recovered_bytes_are_byte_exact(self):
        damage.delete_block(self.root, 0, 2)
        damage.flip_byte(self.root, 2, 1, 10)
        recover_bundle(self.root)
        path = write_recovered(self.root)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), self.pcm_data)


class CliTests(BundleTestBase):
    def test_cli_recover_and_verify_exit_codes(self):
        self.assertEqual(
            cli_main(["delete", "--root", self.root,
                      "--group", "0", "--slot", "2"]), 0
        )
        self.assertEqual(
            cli_main(["recover", "--root", self.root]), 0
        )
        self.assertEqual(
            cli_main(["verify", "--root", self.root]), 0
        )
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "recovered.pcm")
        ))

    def test_cli_unrecoverable_returns_code_2_and_no_output(self):
        cli_main(["delete", "--root", self.root, "--group", "0", "--slot", "0"])
        cli_main(["delete", "--root", self.root, "--group", "0", "--slot", "1"])
        self.assertEqual(
            cli_main(["recover", "--root", self.root]), 2
        )
        self.assertEqual(
            cli_main(["verify", "--root", self.root]), 2
        )
        self.assertFalse(os.path.exists(
            os.path.join(self.root, "recovered.pcm")
        ))


if __name__ == "__main__":
    unittest.main()
