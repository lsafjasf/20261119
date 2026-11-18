"""Verification suite for crossfade management and sample-accurate export.

Run with:  python3 -m unittest discover -s tests -v
"""

import os
import tempfile
import unittest

from audio_editor import (
    EQUAL_POWER,
    LINEAR,
    AutomationPoint,
    Clip,
    Project,
    Source,
    audition,
    export_wav,
    read_wav,
    render_range,
)
from audio_editor import fade as fade_module
from tests.signals import (
    central_window,
    crossfade_sum,
    crossfade_sum_pair,
    peak_abs,
    rms,
    sine_wave,
    to_db,
    white_noise,
)

SAMPLE_RATE = 48000
FADE_LENGTH = 4800  # 100 ms


class FadeCurveTests(unittest.TestCase):
    """Linear vs equal-power peak differences on the two signal classes."""

    def test_in_phase_sine_linear_has_zero_peak_difference(self):
        tone = sine_wave(FADE_LENGTH * 2, 440.0, SAMPLE_RATE)
        mixed = crossfade_sum(tone, FADE_LENGTH, LINEAR, fade_module)
        # Identical in-phase signals under a linear crossfade sum to the
        # original signal at every overlap sample.
        self.assertAlmostEqual(peak_abs(mixed), peak_abs(tone), places=6)
        self.assertLess(abs(to_db(peak_abs(mixed) / peak_abs(tone))), 0.001)

    def test_in_phase_sine_equal_power_peaks_plus_three_db(self):
        tone = sine_wave(FADE_LENGTH * 2, 440.0, SAMPLE_RATE)
        mixed = crossfade_sum(tone, FADE_LENGTH, EQUAL_POWER, fade_module)
        # At the midpoint cos(pi/4) + sin(pi/4) = sqrt(2) -> +3.01 dB.
        midpoint_gain = peak_abs(mixed) / peak_abs(tone)
        self.assertAlmostEqual(midpoint_gain, 2.0 ** 0.5, places=3)
        self.assertAlmostEqual(to_db(midpoint_gain), 3.0103, places=2)

    def test_uncorrelated_noise_linear_dips_three_db_rms(self):
        source_a = white_noise(FADE_LENGTH * 2, seed=7)
        source_b = white_noise(FADE_LENGTH * 2, seed=991)
        mixed = crossfade_sum_pair(
            source_a, source_b, FADE_LENGTH, LINEAR, fade_module)
        reference_rms = rms(source_a)
        midpoint_rms = rms(central_window(mixed))
        # Uncorrelated equal-level signals: midpoint RMS is 1/sqrt(2).
        self.assertAlmostEqual(
            to_db(midpoint_rms / reference_rms), -3.0103, delta=0.25)

    def test_uncorrelated_noise_equal_power_keeps_constant_rms(self):
        source_a = white_noise(FADE_LENGTH * 2, seed=7)
        source_b = white_noise(FADE_LENGTH * 2, seed=991)
        mixed = crossfade_sum_pair(
            source_a, source_b, FADE_LENGTH, EQUAL_POWER, fade_module)
        reference_rms = rms(source_a)
        midpoint_rms = rms(central_window(mixed))
        self.assertAlmostEqual(
            to_db(midpoint_rms / reference_rms), 0.0, delta=0.25)

    def test_equal_power_gains_are_unit_power_everywhere(self):
        out_gains, in_gains = fade_module.fade_gains(
            EQUAL_POWER, FADE_LENGTH)
        for out_gain, in_gain in zip(out_gains, in_gains):
            self.assertAlmostEqual(
                out_gain * out_gain + in_gain * in_gain, 1.0, places=12)
        # Exact endpoint pins guarantee deterministic seam samples.
        self.assertEqual((out_gains[0], in_gains[0]), (1.0, 0.0))
        self.assertEqual((out_gains[-1], in_gains[-1]), (0.0, 1.0))

    def test_linear_gains_sum_to_one_everywhere(self):
        out_gains, in_gains = fade_module.fade_gains(LINEAR, FADE_LENGTH)
        for out_gain, in_gain in zip(out_gains, in_gains):
            self.assertAlmostEqual(out_gain + in_gain, 1.0, places=12)


class CrossfadeGeometryTests(unittest.TestCase):
    """Overlap detection and recomputation when geometry changes."""

    def _overlapping_project(self, mode=LINEAR):
        source_a = Source("a", sine_wave(9600, 440.0, SAMPLE_RATE),
                          SAMPLE_RATE)
        source_b = Source("b", sine_wave(9600, 660.0, SAMPLE_RATE),
                          SAMPLE_RATE)
        project = Project(SAMPLE_RATE, default_fade_mode=mode)
        project.add_clip(Clip("a", source_a, start=0))
        project.add_clip(Clip("b", source_b, start=7200))
        return project

    def test_overlap_produces_crossfade_with_cached_curve(self):
        project = self._overlapping_project()
        xfades = project.get_crossfades()
        self.assertEqual(len(xfades), 1)
        xfade = xfades[0]
        self.assertEqual((xfade.start, xfade.length, xfade.end),
                         (7200, 2400, 9600))
        self.assertEqual(len(xfade.out_gains), 2400)
        self.assertEqual(xfade.out_gains[0], 1.0)
        self.assertEqual(xfade.in_gains[-1], 1.0)

    def test_moving_clip_recomputes_curve(self):
        project = self._overlapping_project()
        before = project.get_crossfades()[0]
        revision_before = project.revision
        project.move_clip("b", new_start=4800)
        self.assertGreater(project.revision, revision_before)
        after = project.get_crossfades()[0]
        self.assertEqual((after.start, after.length), (4800, 4800))
        # Curve at a fixed timeline sample must reflect the new geometry.
        old_gain = before.gain_for("a", 7200)
        new_gain = after.gain_for("a", 7200)
        self.assertNotAlmostEqual(old_gain, new_gain)
        # The new overlap spans [4800, 9600); sample 7200 sits just past
        # its midpoint on a linear outgoing ramp.
        expected_t = (7200 - 4800) / (4800 - 1)
        self.assertAlmostEqual(new_gain, 1.0 - expected_t, places=9)
        # Even-length fades have no exact t=0.5 sample; the two middle
        # samples bracket 0.5 by one normalized step each.
        step = 1.0 / (4800 - 1)
        self.assertAlmostEqual(after.gain_for("a", 7199),
                               0.5 + step / 2.0, places=9)
        self.assertAlmostEqual(after.gain_for("a", 7200),
                               0.5 - step / 2.0, places=9)

    def test_changing_overlap_length_recomputes_curve(self):
        project = self._overlapping_project()
        project.trim_clip("a", new_duration=8400)
        xfade = project.get_crossfades()[0]
        self.assertEqual((xfade.start, xfade.length), (7200, 1200))
        out_gains, in_gains = fade_module.fade_gains(LINEAR, 1200)
        self.assertEqual(xfade.out_gains, out_gains)
        self.assertEqual(xfade.in_gains, in_gains)

    def test_removing_overlap_drops_crossfade(self):
        project = self._overlapping_project()
        project.move_clip("b", new_start=9600)
        self.assertEqual(project.get_crossfades(), [])

    def test_fade_mode_switch_rebuilds_gain_arrays(self):
        project = self._overlapping_project()
        xfade = project.set_fade_mode("a", "b", EQUAL_POWER)
        self.assertEqual(xfade.mode, EQUAL_POWER)
        expected_out, expected_in = fade_module.fade_gains(
            EQUAL_POWER, xfade.length)
        self.assertEqual(xfade.out_gains, expected_out)
        self.assertEqual(xfade.in_gains, expected_in)


class AutomationCascadeTests(unittest.TestCase):
    """Automation points must not outlive their referenced clip."""

    def _project_with_automation(self):
        source = Source("s", sine_wave(1000, 440.0, SAMPLE_RATE),
                        SAMPLE_RATE)
        project = Project(SAMPLE_RATE)
        project.add_clip(Clip("c", source, start=0))
        project.add_automation(AutomationPoint("p1", "c", 100, 0.8))
        project.add_automation(AutomationPoint("p2", "c", 400, 0.2))
        return project

    def test_automation_requires_existing_clip(self):
        project = Project(SAMPLE_RATE)
        with self.assertRaises(ValueError):
            project.add_automation(
                AutomationPoint("ghost", "missing", 0, 1.0))

    def test_deleting_clip_removes_its_automation_points(self):
        project = self._project_with_automation()
        removed = project.delete_clip("c")
        self.assertEqual({p.id for p in removed}, {"p1", "p2"})
        self.assertEqual(project.automation, [])
        self.assertEqual(project.dangling_automation(), [])

    def test_deleting_one_clip_keeps_other_clips_automation(self):
        project = self._project_with_automation()
        source_b = Source("s2", sine_wave(1000, 440.0, SAMPLE_RATE),
                          SAMPLE_RATE)
        project.add_clip(Clip("d", source_b, start=2000))
        project.add_automation(AutomationPoint("p3", "d", 2100, 0.5))
        project.delete_clip("c")
        self.assertEqual([p.id for p in project.automation], ["p3"])
        self.assertEqual(project.dangling_automation(), [])

    def test_automation_interpolation_appears_in_envelope(self):
        project = self._project_with_automation()
        envelope = dict(project.clip_envelope("c"))
        self.assertAlmostEqual(envelope[100], 0.8)
        self.assertAlmostEqual(envelope[400], 0.2)
        midpoint = envelope[250]  # halfway between 0.8 and 0.2
        self.assertAlmostEqual(midpoint, 0.5)


class RenderConsistencyTests(unittest.TestCase):
    """UI envelope, audition and offline WAV export must agree."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        source_a = Source("a", sine_wave(9600, 440.0, SAMPLE_RATE),
                          SAMPLE_RATE)
        source_b = Source("b", sine_wave(9600, 660.0, SAMPLE_RATE),
                          SAMPLE_RATE)
        self.project = Project(SAMPLE_RATE, default_fade_mode=EQUAL_POWER)
        self.project.add_clip(Clip("a", source_a, start=0))
        self.project.add_clip(Clip("b", source_b, start=7200))
        self.project.add_automation(
            AutomationPoint("ap", "a", 7200, 0.9))

    def test_exported_envelope_matches_ui_curve_sample_for_sample(self):
        envelope = dict(self.project.clip_envelope("a"))
        rendered = audition(self.project)
        source_a = self.project.clips["a"].source.samples
        for timeline_sample in range(0, 9600):
            expected_source = source_a[timeline_sample] * envelope[
                timeline_sample]
            if timeline_sample < 7200:
                # Only clip A is active before the overlap.
                self.assertAlmostEqual(
                    rendered[timeline_sample], expected_source, places=9)

    def test_audition_and_export_share_the_exact_same_mix(self):
        path = os.path.join(self.tmpdir.name, "export.wav")
        _, start, end, pre_quant = export_wav(
            self.project, path)
        preview = audition(self.project, start, end)
        self.assertEqual(len(preview), len(pre_quant))
        self.assertEqual(preview, pre_quant)

    def test_seam_sample_positions_are_identical_in_audition_and_wav(self):
        path = os.path.join(self.tmpdir.name, "seam.wav")
        path, start, end, pre_quant = export_wav(
            self.project, path)
        decoded, rate = read_wav(path)
        self.assertEqual(rate, SAMPLE_RATE)
        self.assertEqual(len(decoded), end - start)
        xfade = self.project.get_crossfades()[0]
        seam_first = xfade.start - start
        seam_last = xfade.end - 1 - start
        # The first and last overlap samples carry the pinned endpoint gains.
        self.assertAlmostEqual(xfade.out_gains[0], 1.0)
        self.assertAlmostEqual(xfade.in_gains[0], 0.0)
        self.assertAlmostEqual(xfade.out_gains[-1], 0.0)
        self.assertAlmostEqual(xfade.in_gains[-1], 1.0)
        # Decoded WAV differs from the shared float mix only by int16
        # quantization; seam positions line up sample for sample.
        for index in (seam_first, seam_last):
            self.assertLess(abs(decoded[index] - pre_quant[index]),
                            1.0 / 32767.0 + 1e-12)
        preview = audition(self.project, start, end)
        self.assertEqual(
            [round(v * 32767) for v in preview],
            [round(v * 32767) for v in pre_quant])

    def test_rendered_seam_content_matches_curve_gains(self):
        # Dedicated project without automation, so raw fade gains explain
        # every rendered sample in the overlap region.
        source_a = Source("a2", sine_wave(9600, 440.0, SAMPLE_RATE),
                          SAMPLE_RATE)
        source_b = Source("b2", sine_wave(9600, 660.0, SAMPLE_RATE),
                          SAMPLE_RATE)
        project = Project(SAMPLE_RATE, default_fade_mode=EQUAL_POWER)
        project.add_clip(Clip("a", source_a, start=0))
        project.add_clip(Clip("b", source_b, start=7200))
        rendered = render_range(project, 7200, 9600)
        xfade = project.get_crossfades()[0]
        clip_a = project.clips["a"]
        clip_b = project.clips["b"]
        for offset in range(xfade.length):
            timeline_sample = 7200 + offset
            sample_a = clip_a.source.samples[timeline_sample]
            sample_b = clip_b.source.samples[
                timeline_sample - clip_b.start]
            expected = (
                sample_a * xfade.out_gains[offset]
                + sample_b * xfade.in_gains[offset])
            self.assertAlmostEqual(
                rendered[offset], expected, places=9)

    def test_wav_roundtrip_preserves_sample_count_and_rate(self):
        path = os.path.join(self.tmpdir.name, "round.wav")
        export_wav(self.project, path)
        decoded, rate = read_wav(path)
        self.assertEqual(rate, SAMPLE_RATE)
        # Clip B extends to 7200 + 9600 = 16800.
        self.assertEqual(len(decoded), 16800)


if __name__ == "__main__":
    unittest.main()
