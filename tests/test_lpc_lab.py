from __future__ import annotations

import math
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lpc_lab.codec import (
    FLAG_REJECTED,
    FLAG_STABILIZED,
    _quantize_predictor,
    encode_file,
)
from lpc_lab.dsp import (
    AnalysisConfig,
    analyze_signal,
    autocorrelation,
    levinson_durbin,
    pole_radius,
    reflection_to_predictor,
)
from lpc_lab.signals import sustained_vowel, unvoiced_fricative
from lpc_lab.wavio import read_wav, write_wav

ROOT = Path(__file__).resolve().parents[1]


class LpcCoreTests(unittest.TestCase):
    def test_levinson_recovers_ar2(self) -> None:
        rng = random.Random(42)
        samples = [0.0, 0.0]
        for _ in range(6000):
            samples.append(
                1.6 * samples[-1] - 0.64 * samples[-2] + rng.gauss(0.0, 0.05)
            )
        correlations = autocorrelation(samples, 2)
        predictor, reflection, error, stable, clipped, method = levinson_durbin(
            correlations, 2
        )
        self.assertAlmostEqual(predictor[1], 1.6, delta=0.05)
        self.assertAlmostEqual(predictor[2], -0.64, delta=0.05)
        self.assertTrue(stable)
        self.assertFalse(clipped)
        self.assertEqual(method, "levinson")
        self.assertGreater(error, 0.0)
        rebuilt = reflection_to_predictor(reflection)
        for left, right in zip(predictor, rebuilt):
            self.assertAlmostEqual(left, right, places=10)

    def test_silence_uses_zero_predictor_without_division(self) -> None:
        config = AnalysisConfig(16000, 400, 400, 320, 12)
        analysis = analyze_signal([0.0] * 3200, config)
        self.assertEqual(analysis["frame_count"], 8)
        for frame in analysis["frames"]:
            self.assertTrue(frame["is_silence"])
            self.assertEqual(frame["method"], "zero_energy")
            self.assertEqual(frame["predictor"], [1.0] + [0.0] * 12)
            self.assertTrue(all(value == 0.0 for value in frame["residual"]))

    def test_reflection_quantization_stays_stable(self) -> None:
        predictor = reflection_to_predictor([0.91, -0.73, 0.55, -0.42, 0.31])
        codes, quantized, info = _quantize_predictor(
            predictor, [0.91, -0.73, 0.55, -0.42, 0.31], "rc", 8
        )
        self.assertEqual(len(codes), 5)
        self.assertLess(pole_radius(quantized), 1.0)
        self.assertEqual(info["method"], "rc_inherently_stable")
        self.assertEqual(info["flags"], 0)

    def test_direct_quantization_stabilizes_and_encodes_adjusted_values(self) -> None:
        unstable = [1.0, 3.1, -2.4, 0.5]
        reflection = [3.1, -0.2, 0.1]
        codes, quantized, info = _quantize_predictor(
            unstable, reflection, "direct", 6
        )
        self.assertLess(pole_radius(quantized), 1.0)
        self.assertTrue(info["unstable_seen"])
        self.assertTrue(info["flags"] & FLAG_STABILIZED)
        self.assertFalse(info["flags"] & FLAG_REJECTED)
        self.assertEqual(info["method"], "coefficient_scaling")
        self.assertEqual(len(codes), 3)

    def test_sine_has_stable_poles(self) -> None:
        samples = [
            math.sin(2.0 * math.pi * 440.0 * n / 16000.0) for n in range(3200)
        ]
        config = AnalysisConfig(16000, 400, 400, 320, 12)
        analysis = analyze_signal(samples, config)
        self.assertTrue(all(frame["stable"] for frame in analysis["frames"]))
        self.assertTrue(
            all(frame["pole_radius"] < 1.0 for frame in analysis["frames"])
        )


class CodecTests(unittest.TestCase):
    def _write_probe(self, directory: Path, samples: list[float]) -> Path:
        path = directory / "probe.wav"
        write_wav(path, samples, 16000)
        return path

    def test_standalone_decoder_matches_internal_decoder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = self._write_probe(directory, sustained_vowel(0.35))
            prefix = directory / "vowel"
            config = AnalysisConfig(16000, 400, 400, 320, 12)
            encoded = encode_file(source, prefix, config)
            standalone = directory / "standalone.wav"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "decode_stream.py"),
                    str(encoded.coef_path),
                    str(encoded.residual_path),
                    str(standalone),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                standalone.read_bytes(),
                prefix.with_suffix(".recon.wav").read_bytes(),
            )
            self.assertGreater(encoded.reconstruction_snr_db, 8.0)
            self.assertLess(encoded.stability["max_quantized_pole_radius"], 1.0)

    def test_direct_quantized_stream_decodes_identically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = self._write_probe(directory, sustained_vowel(0.3))
            prefix = directory / "direct"
            config = AnalysisConfig(16000, 400, 400, 320, 12)
            encoded = encode_file(
                source,
                prefix,
                config,
                coefficient_mode="direct",
                coefficient_bits=4,
                residual_bits=8,
            )
            self.assertGreater(encoded.stability["unstable_detected"], 0)
            self.assertGreater(encoded.stability["stabilized"], 0)
            self.assertLess(encoded.stability["max_quantized_pole_radius"], 1.0)
            standalone = directory / "direct-standalone.wav"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "decode_stream.py"),
                    str(encoded.coef_path),
                    str(encoded.residual_path),
                    str(standalone),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                standalone.read_bytes(),
                prefix.with_suffix(".recon.wav").read_bytes(),
            )

    def test_silence_reconstruction_is_exact_and_finite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = self._write_probe(directory, [0.0] * 3200)
            prefix = directory / "silence"
            config = AnalysisConfig(16000, 400, 400, 320, 12)
            encoded = encode_file(source, prefix, config)
            self.assertEqual(encoded.stability["silence_frames"], 8)
            self.assertTrue(all(value == 0.0 for value in encoded.reconstruction))
            self.assertTrue(
                all(math.isfinite(value) for value in encoded.reconstruction)
            )

    def test_coefficient_bytes_grow_with_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = self._write_probe(directory, unvoiced_fricative(0.3))
            low = encode_file(
                source,
                directory / "low",
                AnalysisConfig(16000, 400, 400, 320, 4),
            )
            high = encode_file(
                source,
                directory / "high",
                AnalysisConfig(16000, 400, 400, 320, 16),
            )
            self.assertGreater(
                high.sizes["coefficient_file_bytes"],
                low.sizes["coefficient_file_bytes"],
            )
            self.assertGreater(
                high.sizes["total_kilobits_per_second"],
                low.sizes["total_kilobits_per_second"],
            )


class WavTests(unittest.TestCase):
    def test_wav_round_trip(self) -> None:
        samples = [-0.75, -0.1, 0.0, 0.25, 0.9]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "roundtrip.wav"
            write_wav(path, samples, 16000)
            restored, sample_rate = read_wav(path)
            self.assertEqual(sample_rate, 16000)
            for original, decoded in zip(samples, restored):
                self.assertAlmostEqual(original, decoded, delta=1.0 / 16384.0)


if __name__ == "__main__":
    unittest.main()
