import csv
import json
import os
import sys
import tempfile
import unittest
import wave

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from vowel_synth import SynthParams, VowelSynthesizer, clamp_params
from vowel_synth.analysis import measure_formants, voicing_ratio
from vowel_synth.engine import MAX_RADIUS, resonator_coeffs
from vowel_synth.exporters import (
    render,
    write_trajectory_csv,
    write_trajectory_json,
    write_wav,
)
from vowel_synth.params import VOWEL_PRESETS

FS = 22050


class ResonatorStabilityTests(unittest.TestCase):
    def test_poles_inside_unit_circle_across_grid(self):
        for freq in np.linspace(120, 4500, 30):
            for bw in np.linspace(30, 700, 15):
                _, a = resonator_coeffs(freq, bw, FS)
                radii = np.abs(np.roots(a))
                self.assertTrue(np.all(radii < 1.0), (freq, bw, radii))
                self.assertLessEqual(radii.max(), MAX_RADIUS + 1e-12)

    def test_extreme_params_stay_finite(self):
        synth = VowelSynthesizer(clamp_params(
            SynthParams(f0=500, f1=1100, f2=2900, f3=4400, bw1=30, bw3=800)),
            fs=FS)
        for _ in range(8):
            _, out = synth.process_block()
        self.assertTrue(np.isfinite(out).all())

    def test_ramped_coefficients_remain_stable(self):
        synth = VowelSynthesizer(SynthParams(f1=300, f2=900, f3=2500), fs=FS)
        synth.process_block()
        # Large, abrupt target jump forces a wide within-block coefficient ramp.
        synth.process_block(SynthParams(f1=1000, f2=2600, f3=4200,
                                        bw1=30, bw3=700))
        for section in synth._sections:
            self.assertTrue(section.ramp_history)
            for _b0, a1, a2 in section.ramp_history:
                self.assertLess(abs(a2), 1.0)
                self.assertTrue(
                    np.all(np.abs(np.roots([1.0, a1, a2])) < 1.0))


class StreamingContinuityTests(unittest.TestCase):
    def test_parameter_change_does_not_reset_state(self):
        synth = VowelSynthesizer(SynthParams(f0=120), fs=FS)
        before = [sec.zi.copy() for sec in synth._sections]
        phase_before = synth._phase
        synth.process_block()
        synth.process_block(SynthParams(f0=300, f1=400))
        # Filter memory and oscillator phase persist rather than restarting.
        for sec, old in zip(synth._sections, before):
            self.assertFalse(np.allclose(sec.zi, old) and np.all(sec.zi == 0.0))
        self.assertNotEqual(synth._phase, phase_before)

    def test_smoothing_moves_gradually_toward_target(self):
        synth = VowelSynthesizer(SynthParams(f1=300, smooth_tau=0.1), fs=FS)
        synth.process_block(SynthParams(f1=900))
        first = synth.trajectory[-1]["f1"]
        self.assertLess(first, 900.0)
        for _ in range(60):
            synth.process_block()
        self.assertAlmostEqual(synth.trajectory[-1]["f1"], 900.0, delta=15.0)

    def test_block_boundary_is_continuous(self):
        synth = VowelSynthesizer(SynthParams(), fs=FS, block_size=256)
        chunks = [synth.process_block()[1] for _ in range(4)]
        joined = np.concatenate(chunks)
        # No discontinuity (large sample-to-sample jump) at block seams.
        seams = [chunks[i][-1] - chunks[i + 1][0] for i in range(3)]
        typical = np.median(np.abs(np.diff(joined)))
        for jump in seams:
            self.assertLess(abs(jump), 20 * typical + 1e-6)


class ClampTests(unittest.TestCase):
    def test_ascending_formant_order_enforced(self):
        p = clamp_params(SynthParams(f1=1500, f2=500, f3=400))
        self.assertLess(p.f1, p.f2)
        self.assertLess(p.f2, p.f3)

    def test_limits_applied(self):
        p = clamp_params(SynthParams(f0=5, breathiness=3))
        self.assertGreaterEqual(p.f0, 60)
        self.assertLessEqual(p.breathiness, 1.0)


class AnalysisTests(unittest.TestCase):
    def test_formant_centres_close_to_declared(self):
        params = VOWEL_PRESETS["a"]
        audio, _ = render(params, duration=1.2, fs=FS)
        declared = [(params.f1, params.bw1), (params.f2, params.bw2),
                    (params.f3, params.bw3)]
        results = measure_formants(audio[-8192:], FS, declared)
        errors = [r["error_hz"] for r in results]
        self.assertFalse(any(np.isnan(e) for e in errors))
        self.assertLess(np.mean(errors), 120.0)

    def test_breathiness_lowers_voiced_share(self):
        voiced = VowelSynthesizer(SynthParams(breathiness=0.05), fs=FS)
        breathy = VowelSynthesizer(SynthParams(breathiness=0.9), fs=FS)
        src_v = np.concatenate([voiced.process_block()[0] for _ in range(16)])
        src_b = np.concatenate([breathy.process_block()[0] for _ in range(16)])
        v_ratio = voicing_ratio(src_v, FS, 120.0)["voiced_share"]
        b_ratio = voicing_ratio(src_b, FS, 120.0)["voiced_share"]
        self.assertGreater(v_ratio, b_ratio)
        self.assertGreater(v_ratio, 0.8)


class ExportTests(unittest.TestCase):
    def test_wav_and_trajectory_roundtrip(self):
        audio, trajectory = render(VOWEL_PRESETS["i"], duration=0.5, fs=FS)
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = os.path.join(tmp, "out.wav")
            write_wav(wav_path, audio, FS)
            with wave.open(wav_path) as wf:
                self.assertEqual(wf.getframerate(), FS)
                self.assertEqual(wf.getnchannels(), 1)
                self.assertEqual(wf.getsampwidth(), 2)
                self.assertEqual(wf.getnframes(), audio.shape[0])
            csv_path = os.path.join(tmp, "t.csv")
            write_trajectory_csv(csv_path, trajectory)
            with open(csv_path) as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), len(trajectory))
            self.assertIn("f1", rows[0])
            json_path = os.path.join(tmp, "t.json")
            write_trajectory_json(json_path, trajectory, {"fs": FS})
            with open(json_path) as fh:
                payload = json.load(fh)
            self.assertEqual(payload["meta"]["fs"], FS)

    def test_transition_interpolates_formants(self):
        keyframes = [(0.0, VOWEL_PRESETS["a"]), (1.0, VOWEL_PRESETS["i"])]
        audio, trajectory = render(keyframes, duration=1.0, fs=FS)
        self.assertEqual(audio.shape[0], FS)
        self.assertLess(trajectory[-1]["f1"], trajectory[0]["f1"])
        self.assertNotAlmostEqual(trajectory[0]["f2"], trajectory[-1]["f2"])


if __name__ == "__main__":
    unittest.main()
