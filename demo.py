"""End-to-end demonstration of the crossfade editor.

Run:  python3 demo.py

Prints the linear vs equal-power peak/RMS differences for in-phase sine
waves and uncorrelated noise, exercises clip moves, overlap changes and
automation cascade deletion, then writes WAV files into ./demo_output.
"""

import os

from audio_editor import (
    EQUAL_POWER,
    LINEAR,
    AutomationPoint,
    Clip,
    Project,
    Source,
    audition,
    export_wav,
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
FADE_LENGTH = 4800
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "demo_output")


def measure_row(label, mixed, reference):
    peak_db = to_db(peak_abs(mixed) / peak_abs(reference))
    rms_db = to_db(
        rms(central_window(mixed)) / rms(reference))
    return f"{label:<38} peak {peak_db:+7.3f} dB   mid-rms {rms_db:+7.3f} dB"


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("== Fade peak differences (100 ms overlap) ==")
    tone = sine_wave(FADE_LENGTH * 2, 440.0, SAMPLE_RATE)
    noise_a = white_noise(FADE_LENGTH * 2, seed=7)
    noise_b = white_noise(FADE_LENGTH * 2, seed=991)

    print(measure_row(
        "in-phase sine, linear",
        crossfade_sum(tone, FADE_LENGTH, LINEAR, fade_module), tone))
    print(measure_row(
        "in-phase sine, equal-power",
        crossfade_sum(tone, FADE_LENGTH, EQUAL_POWER, fade_module), tone))
    print(measure_row(
        "uncorrelated noise, linear",
        crossfade_sum_pair(noise_a, noise_b, FADE_LENGTH,
                           LINEAR, fade_module), noise_a))
    print(measure_row(
        "uncorrelated noise, equal-power",
        crossfade_sum_pair(noise_a, noise_b, FADE_LENGTH,
                           EQUAL_POWER, fade_module), noise_a))

    print("\n== Timeline behavior ==")
    source_a = Source("lead", sine_wave(9600, 440.0, SAMPLE_RATE),
                      SAMPLE_RATE)
    source_b = Source("tail", sine_wave(9600, 660.0, SAMPLE_RATE),
                      SAMPLE_RATE)
    project = Project(SAMPLE_RATE, default_fade_mode=LINEAR)
    project.add_clip(Clip("lead", source_a, start=0))
    project.add_clip(Clip("tail", source_b, start=7200))
    project.add_automation(AutomationPoint("ap1", "lead", 7200, 0.9))

    xfade = project.get_crossfades()[0]
    print(f"initial overlap: start={xfade.start} length={xfade.length}")
    project.move_clip("tail", new_start=4800)
    xfade = project.get_crossfades()[0]
    print(f"after moving tail:  start={xfade.start} length={xfade.length} "
          f"(curve recomputed, revision={project.revision})")
    project.trim_clip("lead", new_duration=6000)
    xfade = project.get_crossfades()[0]
    print(f"after trimming lead: start={xfade.start} length={xfade.length}")

    print(f"automation before delete: "
          f"{[p.id for p in project.automation]}")
    removed = project.delete_clip("lead")
    print(f"deleted clip 'lead'; removed points: "
          f"{[p.id for p in removed]}; remaining: "
          f"{[p.id for p in project.automation]}; "
          f"dangling: {len(project.dangling_automation())}")

    print("\n== Audition vs offline export ==")
    export_project = Project(SAMPLE_RATE, default_fade_mode=EQUAL_POWER)
    export_project.add_clip(Clip("lead", source_a, start=0))
    export_project.add_clip(Clip("tail", source_b, start=7200))
    path, start, end, pre_quant = export_wav(
        export_project, os.path.join(OUTPUT_DIR, "crossfade.wav"))
    preview = audition(export_project, start, end)
    print(f"exported {path}: samples {start}..{end} "
          f"({end - start} frames)")
    print(f"audition/export identical float mix: {preview == pre_quant}")
    xfade = export_project.get_crossfades()[0]
    print(f"seam samples: first={xfade.start} last={xfade.end - 1} "
          f"with pinned endpoint gains "
          f"({xfade.out_gains[0]:.1f},{xfade.in_gains[0]:.1f}) -> "
          f"({xfade.out_gains[-1]:.1f},{xfade.in_gains[-1]:.1f})")


if __name__ == "__main__":
    main()
