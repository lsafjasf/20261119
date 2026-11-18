import unittest

from h264_sei.sei import (
    SEI_BUFFERING_PERIOD,
    SEI_PIC_TIMING,
    SEI_RECOVERY_POINT,
    SEI_USER_DATA_UNREGISTERED,
    parse_sei_rbsp,
)
from h264_sei.sps import parse_sps_rbsp
from tests.stream_builder import (
    buffering_period_payload,
    build_sps_rbsp,
    pic_timing_payload,
    recovery_point_payload,
    sei_payload,
    user_data_unregistered_payload,
)


def wrap(payloads):
    return b"".join(payloads) + b"\x80"


class SeiKnownTypesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sps = parse_sps_rbsp(build_sps_rbsp())

    def test_buffering_period(self):
        body = buffering_period_payload()
        messages, warning = parse_sei_rbsp(
            wrap([sei_payload(SEI_BUFFERING_PERIOD, body)]), self.sps
        )
        self.assertIsNone(warning)
        self.assertEqual(len(messages), 1)
        msg = messages[0]
        self.assertTrue(msg.parse_ok)
        self.assertEqual(msg.type_name, "buffering_period")
        self.assertEqual(msg.fields["bp_seq_parameter_set_id"], 0)
        self.assertEqual(msg.fields["nal"]["initial_cpb_removal_delay"], [0x11])
        self.assertEqual(msg.fields["nal"]["initial_cpb_removal_delay_offset"], [0x22])
        self.assertEqual(msg.fields["vcl"]["initial_cpb_removal_delay"], [0x33])

    def test_pic_timing_with_full_timestamp(self):
        body = pic_timing_payload()
        messages, warning = parse_sei_rbsp(
            wrap([sei_payload(SEI_PIC_TIMING, body)]), self.sps
        )
        self.assertIsNone(warning)
        msg = messages[0]
        self.assertEqual(msg.fields["cpb_removal_delay"], 0x05)
        self.assertEqual(msg.fields["dpb_output_delay"], 0x07)
        self.assertEqual(msg.fields["pic_struct_name"], "progressive")
        clock = msg.fields["clock_timestamps"][0]
        self.assertEqual(clock["hours_value"], 9)
        self.assertEqual(clock["minutes_value"], 15)
        self.assertEqual(clock["seconds_value"], 30)
        self.assertEqual(clock["n_frames"], 24)
        self.assertEqual(clock["time_offset"], 0xAA)

    def test_user_data_unregistered(self):
        uuid = bytes(range(16))
        user = b"hello-sei-1234"
        body = user_data_unregistered_payload(uuid, user)
        messages, _ = parse_sei_rbsp(
            wrap([sei_payload(SEI_USER_DATA_UNREGISTERED, body)]), self.sps
        )
        msg = messages[0]
        self.assertTrue(msg.parse_ok)
        self.assertEqual(msg.fields["uuid_iso_iec_11578"], uuid.hex())
        self.assertEqual(msg.fields["user_data"], user)
        self.assertIn("hello", msg.fields["user_data_ascii"])
        self.assertEqual(msg.raw_payload, body)

    def test_recovery_point(self):
        body = recovery_point_payload()
        messages, _ = parse_sei_rbsp(
            wrap([sei_payload(SEI_RECOVERY_POINT, body)]), self.sps
        )
        msg = messages[0]
        self.assertEqual(msg.fields["recovery_frame_cnt"], 3)
        self.assertTrue(msg.fields["exact_match_flag"])
        self.assertFalse(msg.fields["broken_link_flag"])
        self.assertEqual(msg.fields["changing_slice_group_idc"], 2)


class UnknownAndMultiPayloadTest(unittest.TestCase):
    def test_unknown_type_keeps_raw_bytes(self):
        body = bytes([0xDE, 0xAD, 0xBE, 0xEF])
        messages, _ = parse_sei_rbsp(wrap([sei_payload(47, body)]), None)
        msg = messages[0]
        self.assertTrue(msg.is_unknown)
        self.assertEqual(msg.type_name, "unknown(47)")
        self.assertEqual(msg.raw_payload, body)
        self.assertEqual(msg.fields["raw_hex"], body.hex())

    def test_multiple_payloads_same_nal_separately_reported(self):
        rbsp = wrap(
            [
                sei_payload(SEI_RECOVERY_POINT, recovery_point_payload()),
                sei_payload(
                    SEI_USER_DATA_UNREGISTERED,
                    user_data_unregistered_payload(b"u" * 16, b"abc"),
                ),
            ]
        )
        messages, warning = parse_sei_rbsp(rbsp)
        self.assertIsNone(warning)
        self.assertEqual([m.payload_type for m in messages], [6, 5])
        self.assertEqual(messages[1].fields["user_data"], b"abc")

    def test_extended_payload_type_and_size(self):
        # Type 256 -> FF 01 ; size 300 -> FF 2D
        body = b"\x01" * 300
        header = b"\xFF\x01\xFF\x2D"
        messages, warning = parse_sei_rbsp(header + body + b"\x80")
        self.assertIsNone(warning)
        msg = messages[0]
        self.assertEqual(msg.payload_type, 256)
        self.assertEqual(msg.payload_size, 300)
        self.assertEqual(len(msg.raw_payload), 300)
        self.assertTrue(msg.is_unknown)


class FaultIsolationTest(unittest.TestCase):
    def test_truncated_message_does_not_destroy_later_messages(self):
        good = sei_payload(SEI_RECOVERY_POINT, recovery_point_payload())
        # Declares 10 bytes but only provides 3; NAL ends right there.
        truncated = bytes([SEI_RECOVERY_POINT, 10]) + b"\x00\x00\x00"
        messages, warning = parse_sei_rbsp(good + truncated)
        self.assertEqual(messages[0].payload_type, SEI_RECOVERY_POINT)
        self.assertTrue(messages[0].parse_ok)
        self.assertFalse(messages[1].parse_ok)
        self.assertIn("exceeds remaining bytes", messages[1].error)
        self.assertEqual(messages[1].raw_payload, b"\x00\x00\x00")
        self.assertIsNotNone(warning)

    def test_malformed_payload_body_is_isolated(self):
        sps = parse_sps_rbsp(build_sps_rbsp())
        # buffering period body shorter than the HRD widths demands
        bad_body = b"\x00"
        good = sei_payload(SEI_USER_DATA_UNREGISTERED, b"z" * 16 + b"ok")
        rbsp = wrap(
            [
                sei_payload(SEI_BUFFERING_PERIOD, bad_body),
                good,
            ]
        )
        messages, _ = parse_sei_rbsp(rbsp, sps)
        self.assertFalse(messages[0].parse_ok)
        self.assertIn("EndOfBitsError", messages[0].error)
        self.assertTrue(messages[1].parse_ok)
        self.assertEqual(messages[1].fields["user_data"], b"ok")

    def test_user_data_without_uuid_reports_error_keeps_bytes(self):
        messages, _ = parse_sei_rbsp(wrap([sei_payload(5, b"short")]))
        msg = messages[0]
        self.assertFalse(msg.parse_ok)
        self.assertEqual(msg.raw_payload, b"short")

    def test_buffering_period_without_hrd_is_labelled_not_fatal(self):
        messages, warning = parse_sei_rbsp(
            wrap([sei_payload(SEI_BUFFERING_PERIOD, buffering_period_payload())])
        )
        self.assertFalse(messages[0].parse_ok)
        self.assertIn("HRD", messages[0].error)
        self.assertIsNone(warning)

    def test_trailing_bits_warning(self):
        body = recovery_point_payload()
        messages, warning = parse_sei_rbsp(
            sei_payload(SEI_RECOVERY_POINT, body) + b"\x81"
        )
        self.assertTrue(messages[0].parse_ok)
        # 0x81 cannot be distinguished from a truncated next message header;
        # the parser must isolate it instead of corrupting the good message.
        self.assertIsNotNone(warning)
        self.assertEqual(messages[-1].raw_payload, b"\x81")

    def test_invalid_zero_trailing_bytes_warn(self):
        body = recovery_point_payload()
        messages, warning = parse_sei_rbsp(
            sei_payload(SEI_RECOVERY_POINT, body) + b"\x80\x01"
        )
        self.assertTrue(messages[0].parse_ok)
        self.assertIn("invalid trailing", warning)


if __name__ == "__main__":
    unittest.main()
