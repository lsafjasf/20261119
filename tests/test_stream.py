import unittest

from h264_sei.rbsp import remove_emulation_prevention
from h264_sei.stream import extract_access_units, extract_nal_units
from tests.stream_builder import (
    buffering_period_payload,
    build_idr_nal,
    build_pps_nal,
    build_sei_nal,
    build_sps_nal,
    pic_timing_payload,
    recovery_point_payload,
    sei_payload,
    user_data_unregistered_payload,
)
from h264_sei.sei import (
    SEI_BUFFERING_PERIOD,
    SEI_PIC_TIMING,
    SEI_RECOVERY_POINT,
    SEI_USER_DATA_UNREGISTERED,
)


def build_multi_au_stream() -> bytes:
    sps = build_sps_nal()
    pps = build_pps_nal()
    # AU 0: two SEI NAL units, one of them carrying two payloads.
    sei0 = build_sei_nal(
        [
            sei_payload(SEI_BUFFERING_PERIOD, buffering_period_payload()),
            sei_payload(SEI_PIC_TIMING, pic_timing_payload()),
        ]
    )
    sei1 = build_sei_nal(
        [sei_payload(SEI_RECOVERY_POINT, recovery_point_payload())]
    )
    idr0 = build_idr_nal()
    # AU 1: user data unregistered SEI + IDR.
    sei2 = build_sei_nal(
        [
            sei_payload(
                SEI_USER_DATA_UNREGISTERED,
                user_data_unregistered_payload(b"U" * 16, b"second-au"),
            )
        ]
    )
    idr1 = build_idr_nal()
    return b"".join([sps, pps, sei0, sei1, idr0, sei2, idr1])


class AccessUnitGroupingTest(unittest.TestCase):
    def setUp(self):
        self.stream = build_multi_au_stream()
        self.aus = extract_access_units(self.stream)

    def test_two_access_units_with_sei_attached_to_vcl(self):
        self.assertEqual(len(self.aus), 2)
        au0, au1 = self.aus
        self.assertTrue(au0.is_idr)
        self.assertEqual(len(au0.sei_nals), 2)
        self.assertEqual(len(au0.all_sei_messages), 3)
        self.assertEqual(len(au1.sei_nals), 1)
        self.assertEqual(len(au1.all_sei_messages), 1)
        # SEI precedes the VCL NAL inside its access unit.
        self.assertEqual([n.nal_type for n in au1.nals], [6, 5])

    def test_payloads_in_same_au_listed_separately(self):
        au0 = self.aus[0]
        types = [msg.payload_type for _nal, msg in au0.all_sei_messages]
        self.assertEqual(types, [SEI_BUFFERING_PERIOD, SEI_PIC_TIMING,
                                 SEI_RECOVERY_POINT])

    def test_sps_hrd_drives_buffering_period_values(self):
        nals = extract_nal_units(self.stream)
        sps_nal = next(n for n in nals if n.nal_type == 7)
        self.assertIsNotNone(sps_nal.sps)
        self.assertIsNotNone(sps_nal.sps.nal_hrd)
        bp = self.aus[0].all_sei_messages[0][1]
        self.assertTrue(bp.parse_ok)
        self.assertEqual(bp.fields["nal"]["initial_cpb_removal_delay"], [0x11])

    def test_nal_offset_points_at_start_code(self):
        nals = extract_nal_units(self.stream)
        for nal in nals:
            window = self.stream[nal.stream_offset:nal.stream_offset + 4]
            self.assertTrue(window.startswith(b"\x00\x00\x01")
                            or window.startswith(b"\x00\x00\x00\x01"))


class StreamFaultIsolationTest(unittest.TestCase):
    def test_broken_sei_nal_does_not_stop_later_frames(self):
        good_sps = build_sps_nal()
        good_pps = build_pps_nal()
        # Hand-crafted corrupt SEI: valid header, truncated payload declaration.
        corrupt_sei = (
            b"\x00\x00\x00\x01\x06"
            + bytes([SEI_RECOVERY_POINT, 10]) + b"\x00\x00\x80"
        )
        good_sei = build_sei_nal(
            [sei_payload(SEI_RECOVERY_POINT, recovery_point_payload())]
        )
        idr = build_idr_nal()
        stream = b"".join([good_sps, good_pps, corrupt_sei, good_sei, idr])

        aus = extract_access_units(stream)
        nals = [n for au in aus for n in au.nals]
        corrupt = next(n for n in nals if n.nal_type == 6
                       and any(not m.parse_ok for m in n.messages))
        good = next(n for n in nals if n.nal_type == 6
                    and all(m.parse_ok for m in n.messages))
        self.assertFalse(corrupt.messages[0].parse_ok)
        self.assertEqual(corrupt.messages[0].raw_payload, b"\x00\x00")
        self.assertTrue(good.messages[0].parse_ok)
        # The following IDR frame remains fully accessible.
        self.assertTrue(any(n.nal_type == 5 for n in nals))

    def test_corrupt_sps_does_not_break_walk(self):
        # SPS RBSP that ends mid Exp-Golomb symbol.
        bad_sps = b"\x00\x00\x00\x01\x67" + b"\x42\x00\x1e\x00"
        good_sei = build_sei_nal(
            [sei_payload(SEI_RECOVERY_POINT, recovery_point_payload())]
        )
        stream = bad_sps + good_sei + build_idr_nal()
        nals = extract_nal_units(stream)
        self.assertEqual([n.nal_type for n in nals], [7, 6, 5])
        self.assertIn("SPS parse error", nals[0].parse_error)
        self.assertTrue(nals[1].messages[0].parse_ok)

    def test_emulation_prevention_inside_sei_payload(self):
        uuid = b"\x00\x00\x00\x00" + b"q" * 12
        user = b"\x00\x00\x01\x02\x03"
        body = user_data_unregistered_payload(uuid, user)
        nal_bytes = build_sei_nal(
            [sei_payload(SEI_USER_DATA_UNREGISTERED, body)]
        )
        nals = extract_nal_units(nal_bytes)
        msg = nals[0].messages[0]
        self.assertTrue(msg.parse_ok)
        self.assertEqual(msg.fields["user_data"], user)
        # The EBSP on the wire must contain an emulation prevention byte.
        self.assertIn(b"\x00\x00\x03\x01", nals[0].raw_ebsp)
        self.assertEqual(remove_emulation_prevention(nals[0].raw_ebsp[1:]),
                         nals[0].rbsp)


if __name__ == "__main__":
    unittest.main()
