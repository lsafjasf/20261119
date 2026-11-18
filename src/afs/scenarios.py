"""Preset scenarios used to validate detection and suppression behaviour."""

from __future__ import annotations

from dataclasses import dataclass

from .controller import ControllerConfig, NotchController
from .detector import DetectorConfig
from .plant import PlantConfig
from .simulator import ClosedLoopSimulator, DriftSchedule, evaluate
from .sources import SourceConfig


@dataclass
class Scenario:
    name: str
    description: str
    plant: PlantConfig
    detector: DetectorConfig
    source: SourceConfig
    controller: ControllerConfig
    drift: DriftSchedule | None


def _base() -> tuple[PlantConfig, DetectorConfig, SourceConfig, ControllerConfig]:
    return (
        PlantConfig(),
        DetectorConfig(),
        SourceConfig(),
        ControllerConfig(),
    )


def drifting_howl() -> Scenario:
    plant, detector, source, controller = _base()
    plant.loop_gain = 1.12
    plant.resonance_q = 34.0
    plant.clip_drive = 0.6
    source.tone_freq = 440.0
    source.tone_amplitude = 0.015  # weak program material; loop self-excites
    return Scenario(
        name="drifting_howl",
        description="Unstable loop (gain>1) with a slowly drifting room mode",
        plant=plant,
        detector=detector,
        source=source,
        controller=controller,
        drift=DriftSchedule(2350.0, 2650.0, 1.5, 7.0),
    )


def stable_music_tone() -> Scenario:
    plant, detector, source, controller = _base()
    plant.loop_gain = 0.85  # stable loop: no howl can grow
    plant.resonance_freq = 1500.0
    source.tone_freq = 880.0
    source.tone_amplitude = 0.18
    source.noise_floor = 0.003
    return Scenario(
        name="stable_music_tone",
        description="Steady musical pure tone in a stable loop (false-alarm test)",
        plant=plant,
        detector=detector,
        source=source,
        controller=controller,
        drift=None,
    )


def burst_noise() -> Scenario:
    plant, detector, source, controller = _base()
    plant.loop_gain = 0.9
    source.tone_amplitude = 0.0
    source.burst_start_s = 3.0
    source.burst_duration_s = 0.12
    source.noise_floor = 0.003
    return Scenario(
        name="burst_noise",
        description="Short broadband transient in a stable loop (false-alarm test)",
        plant=plant,
        detector=detector,
        source=source,
        controller=controller,
        drift=None,
    )


SCENARIOS = {
    "drifting_howl": drifting_howl,
    "stable_music_tone": stable_music_tone,
    "burst_noise": burst_noise,
}


def build_simulator(scenario: Scenario) -> ClosedLoopSimulator:
    controller = NotchController(scenario.controller, scenario.plant.fs)
    return ClosedLoopSimulator(
        scenario.plant,
        scenario.detector,
        scenario.source,
        controller,
        block_size=scenario.detector.hop,
        drift=scenario.drift,
    )


def run_scenario(name: str):
    scenario = SCENARIOS[name]()
    controlled = build_simulator(scenario).run(enable_suppression=True)
    reference = build_simulator(scenario).run(enable_suppression=False)
    metrics = evaluate(
        controlled, reference, notch_q=scenario.controller.q
    )
    return scenario, controlled, reference, metrics
