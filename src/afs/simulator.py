"""Block-level closed-loop simulation and performance metrics."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .biquad import NotchBank
from .controller import NotchController
from .detector import Detection, DetectorConfig, HowlDetector
from .plant import FeedbackPlant, PlantConfig
from .sources import SourceConfig, SourceGenerator


@dataclass
class DriftSchedule:
    """Linear sweep of the room resonance centre frequency over time."""

    start_hz: float = 2350.0
    end_hz: float = 2700.0
    start_s: float = 1.5
    end_s: float = 6.5

    def freq_at(self, t_s: float) -> float:
        if t_s <= self.start_s:
            return self.start_hz
        if t_s >= self.end_s:
            return self.end_hz
        phase = (t_s - self.start_s) / (self.end_s - self.start_s)
        return self.start_hz + phase * (self.end_hz - self.start_hz)


@dataclass
class SimulationResult:
    fs: float
    source: np.ndarray
    mic: np.ndarray
    speaker: np.ndarray
    block_rms_db: np.ndarray
    block_time_s: np.ndarray
    detection_log: list[tuple[float, list[Detection]]]
    notch_log: list[tuple[float, list]]
    events: list
    plant: FeedbackPlant
    controller: NotchController
    detector: HowlDetector
    metrics: dict = field(default_factory=dict)


class ClosedLoopSimulator:
    def __init__(
        self,
        plant_cfg: PlantConfig,
        detector_cfg: DetectorConfig,
        source_cfg: SourceConfig,
        controller: NotchController,
        block_size: int = 512,
        drift: DriftSchedule | None = None,
    ):
        self.fs = float(plant_cfg.fs)
        self.block_size = int(block_size)
        self.plant = FeedbackPlant(plant_cfg)
        self.detector = HowlDetector(detector_cfg, self.fs)
        self.controller = controller
        self.source = SourceGenerator(source_cfg)
        self.drift = drift
        if detector_cfg.hop != block_size:
            raise ValueError("detector hop must equal simulator block size")

    def run(self, enable_suppression: bool = True) -> SimulationResult:
        total = int(round(self.source.cfg.duration_s * self.fs))
        n_blocks = int(np.ceil(total / self.block_size))
        mic = np.zeros(total)
        speaker = np.zeros(total)
        source_all = np.zeros(total)
        rms_db = np.zeros(n_blocks)
        times = np.zeros(n_blocks)
        detection_log: list[tuple[float, list[Detection]]] = []
        notch_log: list[tuple[float, list]] = []
        events = []

        feedback_hold = np.zeros(self.block_size)
        for block_idx in range(n_blocks):
            start = block_idx * self.block_size
            stop = min(start + self.block_size, total)
            size = stop - start
            t_s = start / self.fs

            if self.drift is not None:
                self.plant.set_resonance(self.drift.freq_at(t_s))

            src = self.source.next_block(self.block_size)[:size]
            mic_block = src + feedback_hold[:size]
            mic_block = self.plant.soft_clip(mic_block)

            if enable_suppression:
                detections = self.detector.process_block(
                    mic_block, self.controller.bank.frequencies()
                )
                new_events = self.controller.update(detections)
                events.extend((t_s, ev) for ev in new_events)
                detection_log.append((t_s, list(detections)))
                notch_log.append((t_s, self.controller.bank.slots()))
                out_block = self.controller.bank.process_block(mic_block)
            else:
                out_block = mic_block

            feedback_block = self.plant.feedback_block(out_block)
            mic[start:stop] = mic_block
            speaker[start:stop] = out_block
            source_all[start:stop] = src
            rms_db[block_idx] = 20.0 * np.log10(
                max(np.sqrt(np.mean(mic_block**2)), 1e-8)
            )
            times[block_idx] = t_s
            feedback_hold = np.pad(
                feedback_block, (0, max(0, self.block_size - size))
            )[: self.block_size]

        result = SimulationResult(
            fs=self.fs,
            source=source_all,
            mic=mic,
            speaker=speaker,
            block_rms_db=rms_db,
            block_time_s=times,
            detection_log=detection_log,
            notch_log=notch_log,
            events=events,
            plant=self.plant,
            controller=self.controller,
            detector=self.detector,
        )
        return result


def _band_rms_db(signal_block: np.ndarray, fs: float, band: tuple[float, float]):
    n = signal_block.shape[0]
    spectrum = np.fft.rfft(signal_block * np.hanning(n))
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    mask = (freqs >= band[0]) & (freqs <= band[1])
    power = np.sum(np.abs(spectrum[mask]) ** 2) / max(mask.sum(), 1)
    return 10.0 * np.log10(max(power, 1e-20))


def first_detection_time(result: SimulationResult) -> float | None:
    for t_s, detections in result.detection_log:
        if detections:
            return float(t_s)
    return None


def first_notch_time(result: SimulationResult) -> float | None:
    for t_s, event in result.events:
        if event.kind == "open":
            return float(t_s)
    return None


def suppression_settling_time(
    result: SimulationResult,
    target_db: float = -35.0,
    hold_blocks: int = 3,
    after_s: float = 0.0,
) -> float | None:
    """First time after ``after_s`` the mic level stays below target."""
    run = 0
    for t_s, level in zip(result.block_time_s, result.block_rms_db):
        if t_s < after_s:
            continue
        if level <= target_db:
            run += 1
            if run >= hold_blocks:
                return float(t_s)
        else:
            run = 0
    return None


def remaining_peak_db(result: SimulationResult, window_s: float = 1.0) -> float:
    """Worst block RMS observed in the final ``window_s`` seconds."""
    mask = result.block_time_s >= (result.block_time_s[-1] - window_s)
    return float(np.max(result.block_rms_db[mask]))


def off_target_band_loss_db(
    controlled: np.ndarray,
    reference: np.ndarray,
    fs: float,
    notch_freqs: np.ndarray,
    notch_q: float,
    guard_bw: float = 3.0,
) -> dict:
    """Spectral energy change outside every notch's guard band.

    Negative values denote loss relative to the unprocessed reference.
    """
    n = min(controlled.shape[0], reference.shape[0])
    controlled, reference = controlled[:n], reference[:n]
    spec_ctrl = np.abs(np.fft.rfft(controlled * np.hanning(n))) ** 2
    spec_ref = np.abs(np.fft.rfft(reference * np.hanning(n))) ** 2
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)

    protected = np.zeros_like(freqs, dtype=bool)
    for f0 in notch_freqs:
        bandwidth = f0 / notch_q
        protected |= (freqs >= f0 - guard_bw * bandwidth / 2.0) & (
            freqs <= f0 + guard_bw * bandwidth / 2.0
        )
    off_target = ~protected & (freqs > 20.0)
    ctrl_energy = float(np.sum(spec_ctrl[off_target]))
    ref_energy = float(np.sum(spec_ref[off_target]))
    loss_db = 10.0 * np.log10(max(ctrl_energy, 1e-20) / max(ref_energy, 1e-20))
    return {
        "loss_db": float(loss_db),
        "ctrl_energy": ctrl_energy,
        "ref_energy": ref_energy,
        "protected_bins": int(protected.sum()),
        "off_target_bins": int(off_target.sum()),
    }


def evaluate(
    controlled: SimulationResult,
    reference: SimulationResult | None = None,
    notch_q: float = 18.0,
    settling_target_db: float = -35.0,
) -> dict:
    """Compute the headline metrics requested for the test report."""
    detect_t = first_detection_time(controlled)
    notch_t = first_notch_time(controlled)
    if detect_t is None:
        settling = None
        latency = None
    else:
        settling = suppression_settling_time(
            controlled, settling_target_db, after_s=detect_t
        )
        latency = settling - detect_t if settling is not None else None
    peak = remaining_peak_db(controlled)
    notch_freqs = controlled.controller.bank.frequencies()
    if reference is not None and notch_freqs.size:
        band_loss = off_target_band_loss_db(
            controlled.speaker, reference.speaker, controlled.fs,
            notch_freqs, notch_q,
        )
    else:
        band_loss = {"loss_db": 0.0, "ctrl_energy": 0.0, "ref_energy": 0.0,
                     "protected_bins": 0, "off_target_bins": 0}
    return {
        "first_detection_s": detect_t,
        "first_notch_s": notch_t,
        "settling_time_s": settling,
        "suppression_latency_s": latency,
        "remaining_peak_db": peak,
        "off_target_loss_db": band_loss["loss_db"],
        "notches_used": int(controlled.controller.bank.active_count),
        "notch_frequencies_hz": [round(float(f), 1) for f in notch_freqs],
        "band_loss_detail": band_loss,
    }
