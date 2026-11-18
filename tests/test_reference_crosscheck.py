"""Cross-check supported SEI types against the independent reference parser."""

import io
import unittest
from contextlib import redirect_stdout

from h264_sei.cli import main as cli_main
from h264_sei.sei import (
    SEI_BUFFERING_PERIOD,
    SEI_PIC_TIMING,
    SEI_RECOVERY_POINT,
    SEI_USER_DATA_UNREGISTERED,
    parse_sei_rbsp,
)
from h264_sei.sps import parse_sps_rbsp
from tests.reference_parser import (
    ref_buffering_period,
    ref_pic_timing,
    ref_recovery_point,
    ref_split_messages,
    ref_user_data_unregistered,
)
from tests.stream_builder import (
    buffering_period_payload,
    build_sps_rbsp,
    pic_timing_payload,
    recovery_point_payload,
    sei_payload,
    user_data_unregistered_payload,
)


class ReferenceCrossCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sps = parse_sps_rbsp(build_sps_rbsp())
        cls.hrd = cls.sps.nal_hrd

    def _check(self, payload_type, body):
        rbsp = sei_payload(payload_type, body) + b"\x80"
        production, warning = parse_sei_rbsp(rbsp, self.sps)
        reference = ref_split_messages(rbsp)
        self.assertIsNone(warning)
        self.assertEqual(len(production), len(reference))
        for prod, ref in zip(production, reference):
            self.assertEqual(prod.payload_type, ref.payload_type)
            self.assertEqual(prod.payload_size, ref.payload_size)
            self.assertEqual(prod.raw_payload, ref.raw_payload)
        return production[0], reference[0]

    def test_recovery_point_matches_reference(self):
        prod, _ref = self._check(SEI_RECOVERY_POINT, recovery_point_payload())
        expected = ref_recovery_point(recovery_point_payload())
        self.assertEqual(prod.fields["recovery_frame_cnt"],
                         expected["recovery_frame_cnt"])
        self.assertEqual(prod.fields["exact_match_flag"],
                         expected["exact_match_flag"])
        self.assertEqual(prod.fields["changing_slice_group_idc"],
                         expected["changing_slice_group_idc"])

    def test_user_data_unregistered_matches_reference(self):
        uuid = bytes.fromhex("0123456789abcdef0123456789abcdef")
        body = user_data_unregistered_payload(uuid, b"cross-check")
        prod, _ref = self._check(SEI_USER_DATA_UNREGISTERED, body)
        expected = ref_user_data_unregistered(body)
        self.assertEqual(prod.fields["uuid_iso_iec_11578"], expected["uuid"])
        self.assertEqual(prod.fields["user_data"], expected["user_data"])

    def test_buffering_period_matches_reference(self):
        body = buffering_period_payload()
        prod, _ref = self._check(SEI_BUFFERING_PERIOD, body)
        expected = ref_buffering_period(body, self.hrd.initial_delay_length)
        self.assertEqual(prod.fields["bp_seq_parameter_set_id"],
                         expected["bp_seq_parameter_set_id"])
        self.assertEqual(prod.fields["nal"]["initial_cpb_removal_delay"],
                         [expected["nal_delay"]])
        self.assertEqual(prod.fields["vcl"]["initial_cpb_removal_delay"],
                         [expected["vcl_delay"]])

    def test_pic_timing_matches_reference(self):
        body = pic_timing_payload()
        prod, _ref = self._check(SEI_PIC_TIMING, body)
        expected = ref_pic_timing(
            body,
            cpb_len=self.hrd.cpb_delay_length,
            dpb_len=self.hrd.dpb_output_delay_length_minus1 + 1,
            time_offset_len=self.hrd.time_offset_length,
        )
        self.assertEqual(prod.fields["cpb_removal_delay"],
                         expected["cpb_removal_delay"])
        self.assertEqual(prod.fields["dpb_output_delay"],
                         expected["dpb_output_delay"])
        clock = prod.fields["clock_timestamps"][0]
        self.assertEqual(clock["hours_value"], expected["hours"])
        self.assertEqual(clock["minutes_value"], expected["minutes"])
        self.assertEqual(clock["seconds_value"], expected["seconds"])
        self.assertEqual(clock["time_offset"], expected["time_offset"])

    def test_extended_headers_agree_on_large_unknown_payload(self):
        body = b"\xAB" * 510
        # type 512 -> FF FF 02 ; size 510 -> FF FF 00
        rbsp = b"\xFF\xFF\x02\xFF\xFF\x00" + body + b"\x80"
        production, _ = parse_sei_rbsp(rbsp)
        reference = ref_split_messages(rbsp)
        self.assertEqual(production[0].payload_type, 512)
        self.assertEqual(production[0].payload_size, 510)
        self.assertEqual(production[0].raw_payload, reference[0].raw_payload)


class CliSmokeTest(unittest.TestCase):
    def setUp(self):
        from tests.test_stream import build_multi_au_stream

        self.path = "/tmp/h264_sei_smoke.h264"
        with open(self.path, "wb") as handle:
            handle.write(build_multi_au_stream())

    def _run(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli_main([self.path, *argv])
        self.assertEqual(code, 0)
        return buf.getvalue()

    def test_list_dist_and_show(self):
        listing = self._run("list")
        self.assertIn("SEI type distribution", listing)
        self.assertIn("buffering_period", listing)
        dist = self._run("dist")
        self.assertIn("total SEI messages", dist)
        detail = self._run("show", "0", "--raw-nal")
        self.assertIn("raw payload (RBSP, hex)", detail)
        self.assertIn("raw NAL unit (EBSP incl. header, hex)", detail)

    def test_show_out_of_range_returns_error_code(self):
        buf = io.StringIO()
        import contextlib
        with contextlib.redirect_stderr(buf):
            code = cli_main([self.path, "show", "99"])
        self.assertEqual(code, 1)
        self.assertIn("out of range", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
