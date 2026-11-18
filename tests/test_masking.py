import unittest

import numpy as np

from critical_band.config import ModelParams
from critical_band.signals import make_signal
from critical_band.analysis import run_analysis
from critical_band.masking import absolute_threshold_db_spl


class TestMaskingModel(unittest.TestCase):
    def test_ath_known_values(self):
        # Terhardt 自由场公式在 1 kHz 约 3.6-4 dB，3-4 kHz 附近最低
        self.assertAlmostEqual(absolute_threshold_db_spl(1000.0),
                               3.6, delta=1.0)
        ath_3k = absolute_threshold_db_spl(3000.0)
        self.assertLess(ath_3k, absolute_threshold_db_spl(200.0))
        self.assertLess(ath_3k, absolute_threshold_db_spl(12000.0))

    def test_loud_tone_is_audible(self):
        params = ModelParams()
        x = make_signal("tone", params.fs, 1.0, normalize=False,
                        tone_freq=1000.0, tone_amp_dbfs=-10.0)
        r = run_analysis(x, params)
        margin = r.thresholds["audible_margin_db"]
        # 1 kHz 位于第 9 带；响音的余量应为正
        self.assertGreater(margin[8], 0.0)

    def test_quiet_tone_below_ath(self):
        params = ModelParams()
        x = make_signal("tone", params.fs, 1.0, normalize=False,
                        tone_freq=1000.0, tone_amp_dbfs=-90.0)
        r = run_analysis(x, params)
        margin = r.thresholds["audible_margin_db"]
        self.assertLess(margin[8], 0.0)

    def test_two_tone_detects_two_tonal_maskers(self):
        params = ModelParams(n_fft=8192, win_length=4096)
        x = make_signal("two_tone", params.fs, 1.0, normalize=False,
                        f1=980.0, f2=1060.0,
                        amp1_dbfs=-16.0, amp2_dbfs=-20.0)
        r = run_analysis(x, params)
        freqs = [m.freq_hz for m in r.thresholds["tonal_maskers"]]
        self.assertTrue(any(abs(f - 980.0) < 20.0 for f in freqs))
        self.assertTrue(any(abs(f - 1060.0) < 20.0 for f in freqs))

    def test_noise_signal_has_noise_maskers(self):
        params = ModelParams()
        x = make_signal("noise", params.fs, 1.0, normalize=False,
                        noise_amp_dbfs=-15.0)
        r = run_analysis(x, params)
        self.assertGreater(len(r.thresholds["noise_maskers"]), 10)

    def test_threshold_above_ath_always(self):
        params = ModelParams()
        x = make_signal("tone", params.fs, 1.0, normalize=False,
                        tone_freq=1000.0, tone_amp_dbfs=-20.0)
        r = run_analysis(x, params)
        diff = (r.thresholds["global_threshold_db"]
                - r.thresholds["ath_db"])
        np.testing.assert_array_less(-0.01, diff)

    def test_parameter_override_changes_threshold(self):
        params = ModelParams()
        x = make_signal("tone", params.fs, 1.0, normalize=False,
                        tone_freq=1000.0, tone_amp_dbfs=-20.0)
        base = run_analysis(x, params).thresholds["global_threshold_db"]
        steep = run_analysis(
            x, params.with_changes(spread_up_db_bark=30.0)
        ).thresholds["global_threshold_db"]
        # 更陡的高频扩散 -> 远离掩蔽声的频带阈值更低（接近 ATH）
        self.assertLess(np.sum(steep[12:]), np.sum(base[12:]))


if __name__ == "__main__":
    unittest.main()
