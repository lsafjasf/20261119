import unittest

import numpy as np

from critical_band.config import ModelParams
from critical_band.signals import make_tone, make_broadband_noise
from critical_band.spectrum import stft, get_window, window_factors
from critical_band.analysis import run_analysis, band_energy_invariance


class TestWindowCompensation(unittest.TestCase):
    def test_rect_window_unity_gain(self):
        w = get_window("rect", 1024)
        f = window_factors(w)
        self.assertAlmostEqual(f["coherent_gain"], 1.0)
        self.assertAlmostEqual(f["enbw_bins"], 1.0, places=6)

    def test_hann_known_enbw(self):
        w = get_window("hann", 4096)
        f = window_factors(w)
        # Hann 的 ENBW 理论值约 1.5 bin
        self.assertAlmostEqual(f["enbw_bins"], 1.5, delta=0.02)

    def test_amplitude_spectrum_recovers_tone_level(self):
        fs = 48000.0
        # 1007.8125 = 86*fs/4096，恰好落在 bin 上，无主瓣展宽误差
        f_on_bin = 86 * fs / 4096
        x = make_tone(f_on_bin, -12.0, fs, 1.0)
        params = ModelParams(fs=fs, n_fft=4096, win_length=4096, window="rect")
        frames = stft(x, params)
        k = int(np.argmin(np.abs(frames.freqs - f_on_bin)))
        peak_amp = np.max(frames.amp_spec[:, k])
        self.assertAlmostEqual(20 * np.log10(peak_amp), -12.0, delta=0.2)

    def test_parseval_mean_square(self):
        fs = 48000.0
        x = make_broadband_noise(-18.0, fs, 1.0, seed=1)
        params = ModelParams(fs=fs, n_fft=2048, win_length=2048,
                             window="rect")
        frames = stft(x, params)
        frame_rms = np.mean(np.sum(frames.power_spec, axis=1))
        # 忽略两端补零帧，比较中间帧的均方值
        self.assertAlmostEqual(frame_rms, np.mean(x ** 2), delta=2e-3)


class TestFFTInvariance(unittest.TestCase):
    def test_band_energy_invariant_to_nfft_tone(self):
        # 带界 bin 在不同 FFT 网格间重新归属，逐带允许 <=1 dB；
        # 总能量另有严格的零填充测试
        params = ModelParams(fs=48000.0, n_fft=4096, win_length=2048)
        x = make_tone(1000.0, -20.0, params.fs, 1.0)
        check = band_energy_invariance(x, params,
                                       n_fft_list=(2048, 4096, 8192))
        self.assertLess(check["max_abs_diff_db"], 1.0)

    def test_band_energy_invariant_to_nfft_noise(self):
        params = ModelParams(fs=48000.0, n_fft=4096, win_length=2048)
        x = make_broadband_noise(-20.0, params.fs, 2.0, seed=7)
        check = band_energy_invariance(x, params,
                                       n_fft_list=(2048, 4096, 8192))
        self.assertLess(check["max_abs_diff_db"], 1.0)

    def test_total_energy_invariant_to_nfft_tone_offbin(self):
        # 非 bin 上频率：不同 n_fft 下全部 24 带总能量应高度一致
        params = ModelParams(fs=48000.0, n_fft=4096, win_length=2048)
        x = make_tone(1234.0, -18.0, params.fs, 1.0)
        check = band_energy_invariance(x, params,
                                       n_fft_list=(2048, 4096, 8192))
        totals = 10 * np.log10(np.sum(check["band_power"], axis=1) + 1e-20)
        self.assertLess(np.max(totals) - np.min(totals), 0.05)

    def test_zero_padding_does_not_change_total_energy(self):
        base = ModelParams(fs=48000.0, n_fft=2048, win_length=2048)
        zp = base.with_changes(n_fft=8192)
        x = make_tone(1234.0, -15.0, base.fs, 1.0)
        e1 = np.sum(run_analysis(x, base).mean_band_power)
        e2 = np.sum(run_analysis(x, zp).mean_band_power)
        self.assertAlmostEqual(10 * np.log10(e2 / e1), 0.0, delta=0.05)


if __name__ == "__main__":
    unittest.main()
