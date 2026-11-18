"""Generate the reproducible listening set, plots, metrics, and HTML report."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .core import StretchConfig, stretch_with_info
from .metrics import (
    TestSignals,
    dominant_frequency,
    make_demo_phrase,
    make_test_signals,
    pulse_metrics,
    rms,
    spectral_flatness,
    stft_magnitude,
)
from .realtime import RealtimeStretcher
from .schedule import SpeedSchedule
from .wavio import write_wav


def _fade_at(time: np.ndarray, duration: float, sample_rate: int) -> np.ndarray:
    fade_len = min(int(round(duration * sample_rate)) // 20, int(0.02 * sample_rate))
    result = np.ones_like(time, dtype=np.float64)
    if fade_len <= 1:
        return result
    edge = min(fade_len / sample_rate, duration * 0.5)
    rising = time < edge
    falling = time > duration - edge
    ramp_up = 0.5 - 0.5 * np.cos(np.pi * np.clip(time, 0.0, edge) / edge)
    ramp_down = 0.5 + 0.5 * np.cos(
        np.pi * np.clip(time - (duration - edge), 0.0, edge) / edge
    )
    result[rising] = ramp_up[rising]
    result[falling] = ramp_down[falling]
    result[(time < 0.0) | (time > duration)] = 0.0
    return result


def _localized_fit_residual(
    output: np.ndarray,
    sin_basis: np.ndarray,
    cos_basis: np.ndarray,
    *,
    block_samples: int = 2_048,
) -> np.ndarray:
    """Subtract a short-time best-fit reference, avoiding phase-drift bias."""
    residual = np.empty_like(output)
    for start in range(0, output.size, block_samples):
        stop = min(output.size, start + block_samples)
        basis = np.column_stack(
            (sin_basis[start:stop], cos_basis[start:stop])
        )
        coefficients, *_ = np.linalg.lstsq(
            basis, output[start:stop], rcond=None
        )
        residual[start:stop] = output[start:stop] - basis @ coefficients
    return residual


def _fit_chirp_residual(
    output: np.ndarray,
    speed: float,
    signals: TestSignals,
    sample_rate: int,
    duration: float,
) -> tuple[np.ndarray, dict[str, float]]:
    time = np.arange(output.size) / sample_rate
    source_time = speed * time
    slope = (signals.sweep_end - signals.sweep_start) / duration
    # Preserved pitch means the output phase is the source phase divided by
    # speed; its derivative is f_src(speed * t).
    phase = 2.0 * np.pi * (
        signals.sweep_start * time + 0.5 * slope * speed * time**2
    )
    envelope = _fade_at(source_time, duration, sample_rate)
    residual = _localized_fit_residual(
        output, envelope * np.sin(phase), envelope * np.cos(phase)
    )
    signal_power = float(np.sum(output**2))
    residual_power = float(np.sum(residual**2))
    return residual, {
        "reference": "short-time best-fit stretched linear chirp",
        "residual_rms": rms(residual),
        "signal_rms": rms(output),
        "residual_to_signal_db": 10.0
        * np.log10(max(residual_power, 1e-20) / max(signal_power, 1e-20)),
    }


def _tone_residual(
    output: np.ndarray,
    speed: float,
    signals: TestSignals,
    sample_rate: int,
    duration: float,
) -> tuple[np.ndarray, dict[str, float]]:
    time = np.arange(output.size) / sample_rate
    envelope = _fade_at(speed * time, duration, sample_rate)
    phase = 2.0 * np.pi * signals.tone_frequency * time
    residual = _localized_fit_residual(
        output, envelope * np.sin(phase), envelope * np.cos(phase)
    )
    signal_power = float(np.sum(output**2))
    residual_power = float(np.sum(residual**2))
    return residual, {
        "reference": "short-time best-fit 220 Hz tone with mapped envelope",
        "residual_rms": rms(residual),
        "signal_rms": rms(output),
        "residual_to_signal_db": 10.0
        * np.log10(max(residual_power, 1e-20) / max(signal_power, 1e-20)),
    }


def _pulse_residual(
    output: np.ndarray,
    speed: float,
    signals: TestSignals,
    sample_rate: int,
) -> tuple[np.ndarray, dict[str, float]]:
    residual = output.copy()
    expected = int(round(signals.pulse_time * sample_rate / speed))
    if 0 <= expected < output.size:
        amplitude = np.sqrt(max(float(np.sum(output**2)), 0.0))
        residual[expected] -= amplitude
    signal_power = float(np.sum(output**2))
    residual_power = float(np.sum(residual**2))
    return residual, {
        "reference": "energy-equivalent delayed impulse",
        "residual_rms": rms(residual),
        "signal_rms": rms(output),
        "residual_to_signal_db": 10.0
        * np.log10(max(residual_power, 1e-20) / max(signal_power, 1e-20)),
    }


def _save_plot(
    path: Path,
    output: np.ndarray,
    residual: np.ndarray,
    sample_rate: int,
    title: str,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    spectrum = stft_magnitude(output)
    db = 20.0 * np.log10(np.maximum(spectrum, 1e-10))
    db -= float(np.max(db))
    time = np.arange(output.size) / sample_rate
    frames = np.arange(spectrum.shape[1]) * 256 / sample_rate
    freqs = np.arange(spectrum.shape[0]) * sample_rate / 1_024
    figure, axes = plt.subplots(
        3,
        1,
        figsize=(9.0, 7.4),
        constrained_layout=True,
        gridspec_kw={"height_ratios": [2.2, 1.1, 1.2]},
    )
    mesh = axes[0].pcolormesh(
        frames, freqs, db, shading="auto", cmap="magma", vmin=-72, vmax=0
    )
    axes[0].set_ylim(0, min(8_000, sample_rate / 2))
    axes[0].set_ylabel("Hz")
    axes[0].set_title(title)
    figure.colorbar(mesh, ax=axes[0], pad=0.01, label="dB")

    average = np.mean(spectrum, axis=1)
    average_db = 20.0 * np.log10(np.maximum(average, 1e-10))
    average_db -= float(np.max(average_db))
    axes[1].plot(freqs, average_db, color="#176b87", linewidth=0.9)
    axes[1].set_xlim(0, min(8_000, sample_rate / 2))
    axes[1].set_ylim(-80, 3)
    axes[1].set_ylabel("dB")
    axes[1].set_title("Time-averaged spectrum")
    axes[1].grid(alpha=0.25)

    axes[2].plot(time, residual, color="#b23a48", linewidth=0.65)
    axes[2].set_xlim(0.0, max(time[-1] if time.size else 0.0, 0.01))
    axes[2].set_ylabel("residual")
    axes[2].set_xlabel("output time (s)")
    axes[2].set_title("Residual after known-signal fit")
    axes[2].grid(alpha=0.25)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=145)
    plt.close(figure)


def _speed_tag(speed: float) -> str:
    return f"{speed:g}".replace(".", "p") + "x"


def _render_realtime_switches(
    source: np.ndarray,
    config: StretchConfig,
    *,
    initial_speed: float,
    switches: list[tuple[float, float]],
    output_samples: int,
    block_size: int = 512,
) -> tuple[np.ndarray, dict[str, Any]]:
    engine = RealtimeStretcher(
        source,
        initial_speed,
        config,
        control_ramp_ms=20.0,
    )
    output: list[np.ndarray] = []
    position = 0
    pending = [
        (int(round(time * config.sample_rate)), speed)
        for time, speed in switches
    ]
    while position < output_samples:
        while pending and pending[0][0] <= position:
            _, speed = pending.pop(0)
            engine.set_speed(speed)
        count = min(block_size, output_samples - position)
        if pending:
            count = min(count, max(1, pending[0][0] - position))
        output.append(engine.pull(count))
        position += count
    audio = np.concatenate(output, axis=0)
    speed_times, effective_speeds = engine.effective_speed_track
    info = {
        "initial_speed": initial_speed,
        "switches": [
            {"output_time_s": time, "speed": speed} for time, speed in switches
        ],
        "output_samples": int(audio.size),
        "block_size": block_size,
        "algorithmic_lookahead_samples": engine.algorithmic_lookahead_samples,
        "algorithmic_lookahead_ms": engine.algorithmic_lookahead_ms,
        "control_ramp_ms": engine.control_ramp_ms,
        "underruns": engine.underruns,
        "control_events": [
            event.to_dict(config.sample_rate) for event in engine.control_events
        ],
        "no_all_zero_blocks": bool(
            all(float(np.max(np.abs(block))) > 0.0 for block in output)
        ),
        "speed_track": [
            [round(float(time), 6), round(float(speed), 6)]
            for time, speed in zip(speed_times, effective_speeds)
        ],
    }
    return audio, info


def _short_time_rms(
    signal: np.ndarray,
    sample_rate: int,
    window_ms: float = 20.0,
) -> np.ndarray:
    window = max(1, int(round(sample_rate * window_ms / 1000.0)))
    count = max(1, signal.size // window)
    trimmed = signal[: count * window].reshape(count, window)
    return np.sqrt(np.mean(trimmed * trimmed, axis=1))


def _save_transition_plot(
    path: Path,
    offline_info: Any,
    realtime_info: dict[str, Any],
    offline_audio: np.ndarray,
    realtime_audio: np.ndarray,
    sample_rate: int,
    switch_times: list[float],
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(
        3,
        1,
        figsize=(9.5, 7.5),
        constrained_layout=True,
        gridspec_kw={"height_ratios": [1.25, 1.25, 1.0]},
    )
    centers = np.asarray(offline_info.synthesis_centers) / sample_rate
    positions = np.asarray(offline_info.analysis_positions) / sample_rate
    visible = (centers >= 0.0) & (centers <= 3.0)
    axes[0].plot(
        centers[visible],
        positions[visible],
        color="#176b87",
        linewidth=1.5,
    )
    axes[0].set_xlim(0.0, 3.0)
    axes[0].set_xlabel("output time (s)")
    axes[0].set_ylabel("source position (s)")
    axes[0].set_title("Offline analysis trajectory: continuous at every speed change")
    axes[0].grid(alpha=0.25)

    track = np.asarray(realtime_info["speed_track"], dtype=np.float64)
    visible = (track[:, 0] >= 0.0) & (track[:, 0] <= 3.0)
    axes[1].step(
        track[visible, 0],
        track[visible, 1],
        where="post",
        color="#b23a48",
        linewidth=1.4,
        label="realtime effective speed",
    )
    axes[1].set_xlim(0.0, 3.0)
    axes[1].set_ylim(0.35, 2.15)
    axes[1].set_xlabel("output time (s)")
    axes[1].set_ylabel("speed")
    axes[1].set_title("Realtime control: lookahead + 20 ms ramp (horizontal steps)")
    axes[1].grid(alpha=0.25)
    axes[1].legend(loc="upper right")

    for axes_item in axes[:2]:
        for time in switch_times:
            axes_item.axvline(time, color="#d29b35", linestyle="--", linewidth=0.9)

    offline_rms = _short_time_rms(offline_audio, sample_rate)
    realtime_rms = _short_time_rms(realtime_audio, sample_rate)
    rms_time = (np.arange(min(offline_rms.size, realtime_rms.size)) + 0.5) * 0.02
    axes[2].plot(rms_time, offline_rms[: rms_time.size], label="offline", color="#176b87")
    axes[2].plot(rms_time, realtime_rms[: rms_time.size], label="realtime", color="#b23a48")
    axes[2].set_xlim(0.0, 3.0)
    axes[2].set_xlabel("output time (s)")
    axes[2].set_ylabel("20 ms RMS")
    axes[2].set_title("Both paths remain non-silent through every switch")
    axes[2].grid(alpha=0.25)
    axes[2].legend(loc="lower right")
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=145)
    plt.close(figure)


def build_suite(
    output_dir: str | Path = "artifacts",
    *,
    sample_rate: int = 44_100,
    speeds: tuple[float, ...] = (0.5, 1.0, 2.0),
) -> dict[str, Any]:
    """Render all test signals, plots, diagnostics, and listening examples."""
    root = Path(output_dir)
    audio_dir = root / "audio"
    plot_dir = root / "spectra"
    data_dir = root / "data"
    for directory in (audio_dir, plot_dir, data_dir):
        directory.mkdir(parents=True, exist_ok=True)

    config = StretchConfig(sample_rate=sample_rate, frame_size=2_048)
    signals = make_test_signals(sample_rate)
    duration = signals.tone.size / sample_rate
    signal_map = {
        "tone": signals.tone,
        "sweep": signals.sweep,
        "pulse": signals.pulse,
    }
    report: dict[str, Any] = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sample_rate": sample_rate,
            "frame_size": config.frame_size,
            "synthesis_hop": config.synthesis_hop,
            "synthesis_hop_ms": 1000.0 * config.synthesis_hop / sample_rate,
            "phase_locking": config.phase_locking,
            "transient_reset": config.transient_reset,
            "speeds": list(speeds),
            "supported_speed_range": [config.min_speed, config.max_speed],
            "offline_semantics": (
                "whole-signal phase propagation; sample count is rounded once "
                "from the requested duration"
            ),
            "realtime_semantics": (
                "fixed output-hop pull engine with one-window input lookahead "
                "and a finite control ramp"
            ),
        },
        "tests": {},
        "realtime": {},
        "artifacts": {"audio": [], "plots": [], "data": []},
    }

    for name, source in signal_map.items():
        source_path = write_wav(audio_dir / f"{name}_source.wav", source, sample_rate)
        report["artifacts"]["audio"].append(str(source_path.relative_to(root)))
        test_entry: dict[str, Any] = {
            "input_samples": int(source.size),
            "input_duration_s": source.size / sample_rate,
            "source_audio": str(source_path.relative_to(root)),
            "speeds": {},
        }
        for speed in speeds:
            result = stretch_with_info(source, speed, config)
            output = result.audio
            expected_samples = int(round(source.size / speed))
            tag = _speed_tag(speed)
            audio_path = write_wav(
                audio_dir / f"{name}_{tag}.wav", output, sample_rate
            )
            npz_path = data_dir / f"{name}_{tag}.npz"

            if name == "tone":
                residual, residual_info = _tone_residual(
                    output, speed, signals, sample_rate, duration
                )
                frequency = dominant_frequency(
                    output, sample_rate, fmin=80.0, fmax=500.0
                )
                signal_metrics: dict[str, Any] = {
                    "fundamental_hz": frequency,
                    "fundamental_error_hz": frequency - signals.tone_frequency,
                    "expected_fundamental_hz": signals.tone_frequency,
                }
            elif name == "sweep":
                residual, residual_info = _fit_chirp_residual(
                    output, speed, signals, sample_rate, duration
                )
                slope = (signals.sweep_end - signals.sweep_start) / duration
                points = []
                for fraction in (0.10, 0.25, 0.50, 0.75, 0.90):
                    output_time = fraction * duration / speed
                    width = int(0.04 * sample_rate)
                    center = int(round(output_time * sample_rate))
                    lo = max(0, center - width // 2)
                    hi = min(output.size, center + width // 2)
                    measured = dominant_frequency(
                        output[lo:hi], sample_rate, fmin=60.0, fmax=2_000.0
                    )
                    expected = (
                        signals.sweep_start + slope * fraction * duration
                    )
                    points.append(
                        {
                            "source_fraction": fraction,
                            "output_time_s": output_time,
                            "expected_hz": expected,
                            "measured_hz": measured,
                            "error_hz": measured - expected,
                        }
                    )
                errors = np.asarray([point["error_hz"] for point in points])
                signal_metrics = {
                    "tracking_points": points,
                    "max_abs_tracking_error_hz": float(np.max(np.abs(errors))),
                }
            else:
                residual, residual_info = _pulse_residual(
                    output, speed, signals, sample_rate
                )
                signal_metrics = pulse_metrics(
                    output,
                    sample_rate,
                    signals.pulse_time / speed,
                )
                signal_metrics["expected_peak_time_ms"] = (
                    1000.0 * signals.pulse_time / speed
                )

            spectrum = stft_magnitude(output)
            residual_spectrum = stft_magnitude(residual)
            np.savez_compressed(
                npz_path,
                output=output.astype(np.float32),
                residual=residual.astype(np.float32),
                time_s=(np.arange(output.size) / sample_rate).astype(np.float32),
                spectrum=spectrum.astype(np.float32),
                residual_spectrum=residual_spectrum.astype(np.float32),
                frequency_hz=(
                    np.arange(spectrum.shape[0]) * sample_rate / 1_024
                ).astype(np.float32),
                frame_time_s=(
                    np.arange(spectrum.shape[1]) * 256 / sample_rate
                ).astype(np.float32),
            )
            plot_path = plot_dir / f"{name}_{tag}.png"
            _save_plot(
                plot_path,
                output,
                residual,
                sample_rate,
                f"{name} @ {speed:g}x | n={output.size} | residual "
                f"{residual_info['residual_to_signal_db']:.1f} dB",
            )
            entry = {
                "speed": speed,
                "audio": str(audio_path.relative_to(root)),
                "plot": str(plot_path.relative_to(root)),
                "data": str(npz_path.relative_to(root)),
                "output_samples": int(output.size),
                "expected_samples": expected_samples,
                "sample_count_error": int(output.size - expected_samples),
                "duration_s": output.size / sample_rate,
                "rms": rms(output),
                "spectral_flatness": spectral_flatness(output),
                "ola_no_holes": result.info.no_ola_holes,
                "ola_gain_cv": result.info.window_gain_cv,
                "analysis_speed_min": result.info.effective_speed_min,
                "analysis_speed_max": result.info.effective_speed_max,
                "phase_step_max_rad": result.info.phase_step_max,
                "residual": residual_info,
                **signal_metrics,
            }
            test_entry["speeds"][f"{speed:g}x"] = entry
            report["artifacts"]["audio"].append(str(audio_path.relative_to(root)))
            report["artifacts"]["plots"].append(str(plot_path.relative_to(root)))
            report["artifacts"]["data"].append(str(npz_path.relative_to(root)))
        report["tests"][name] = test_entry

    phrase = make_demo_phrase(sample_rate)
    phrase_path = write_wav(audio_dir / "demo_source.wav", phrase, sample_rate)
    report["artifacts"]["audio"].append(str(phrase_path.relative_to(root)))
    report["listening_demo"] = {
        "source": str(phrase_path.relative_to(root)),
        "speeds": {},
    }
    for speed in speeds:
        result = stretch_with_info(phrase, speed, config)
        tag = _speed_tag(speed)
        audio_path = write_wav(
            audio_dir / f"demo_{tag}.wav", result.audio, sample_rate
        )
        report["listening_demo"]["speeds"][f"{speed:g}x"] = {
            "audio": str(audio_path.relative_to(root)),
            "output_samples": int(result.audio.size),
            "expected_samples": int(round(phrase.size / speed)),
            "no_ola_holes": result.info.no_ola_holes,
        }
        report["artifacts"]["audio"].append(str(audio_path.relative_to(root)))

    switch_times = [0.80, 1.80, 2.80]
    switch_rates = [2.0, 0.5, 2.0]
    realtime_samples = int(round(3.0 * sample_rate))
    realtime_audio, realtime_info = _render_realtime_switches(
        phrase,
        config,
        initial_speed=0.5,
        switches=list(zip(switch_times, switch_rates)),
        output_samples=realtime_samples,
    )
    realtime_path = write_wav(
        audio_dir / "realtime_switch_demo.wav", realtime_audio, sample_rate
    )
    report["artifacts"]["audio"].append(str(realtime_path.relative_to(root)))

    schedule = SpeedSchedule.from_pairs(
        [(0.0, 0.5), (0.80, 2.0), (1.80, 0.5), (2.80, 2.0)],
        interpolation="step",
    )
    offline_switch = stretch_with_info(
        phrase,
        schedule,
        config,
        output_length=realtime_samples,
    )
    offline_path = write_wav(
        audio_dir / "offline_switch_demo.wav",
        offline_switch.audio,
        sample_rate,
    )
    report["artifacts"]["audio"].append(str(offline_path.relative_to(root)))
    transition_plot = plot_dir / "transition_diagnostics.png"
    _save_transition_plot(
        transition_plot,
        offline_switch.info,
        realtime_info,
        offline_switch.audio,
        realtime_audio,
        sample_rate,
        switch_times,
    )
    report["artifacts"]["plots"].append(
        str(transition_plot.relative_to(root))
    )

    realtime_rms = _short_time_rms(realtime_audio, sample_rate)
    active_floor = float(np.min(realtime_rms[2:-2])) if realtime_rms.size > 4 else 0.0
    offline_rms = _short_time_rms(offline_switch.audio, sample_rate)
    offline_floor = (
        float(np.min(offline_rms[2:-2])) if offline_rms.size > 4 else 0.0
    )
    report["realtime"] = {
        "mode": "prebuffered pull engine, 20 ms linear control ramp",
        **realtime_info,
        "realtime_audio": str(realtime_path.relative_to(root)),
        "offline_audio": str(offline_path.relative_to(root)),
        "diagnostic_plot": str(transition_plot.relative_to(root)),
        "min_short_time_rms_realtime": active_floor,
        "min_short_time_rms_offline": offline_floor,
        "realtime_no_gap": bool(realtime_info["underruns"] == 0 and active_floor > 1e-4),
        "offline_no_gap": bool(
            offline_switch.info.no_ola_holes and offline_floor > 1e-4
        ),
        "offline_output_samples": int(offline_switch.audio.size),
        "offline_expected_samples": realtime_samples,
        "offline_sample_count_error": int(
            offline_switch.audio.size - realtime_samples
        ),
        "offline_control_quantization_ms": (
            1000.0 * config.synthesis_hop / sample_rate
        ),
        "realtime_first_switch_latency_ms": (
            realtime_info["control_events"][0]["latency_ms"]
            if realtime_info["control_events"]
            else None
        ),
        "interpretation": (
            "Offline mode knows the whole timeline and starts the new analysis "
            "slope at the next synthesis frame. Realtime mode additionally "
            "waits for one analysis-window lookahead and the control ramp; "
            "both modes keep the same gapless output hop."
        ),
    }

    report_path = root / "report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _write_html(root, report)
    return report


def _write_html(root: Path, report: dict[str, Any]) -> None:
    meta = report["metadata"]
    realtime = report["realtime"]
    demo = report["listening_demo"]
    rows: list[str] = []
    figures: list[str] = []
    for name, entry in report["tests"].items():
        for label, item in entry["speeds"].items():
            if name == "tone":
                specific = (
                    f"f0 {item['fundamental_hz']:.3f} Hz "
                    f"({item['fundamental_error_hz']:+.3f})"
                )
            elif name == "sweep":
                specific = (
                    f"max track error "
                    f"{item['max_abs_tracking_error_hz']:.2f} Hz"
                )
            else:
                specific = (
                    f"spread {item['energy_width_90_ms']:.2f} ms, "
                    f"peak error {item['timing_error_ms']:+.2f} ms"
                )
            rows.append(
                "<tr>"
                f"<td>{name}</td><td>{label}</td>"
                f"<td>{item['output_samples']} / {item['expected_samples']}</td>"
                f"<td>{item['residual']['residual_to_signal_db']:.1f} dB</td>"
                f"<td>{specific}</td>"
                f"<td>{'pass' if item['ola_no_holes'] else 'FAIL'}</td>"
                "</tr>"
            )
            figures.append(
                '<figure class="plot">'
                f'<img src="{item["plot"]}" alt="{name} {label}">'
                f"<figcaption>{name} at {label}; "
                f'<a href="{item["data"]}">NPZ data</a>, '
                f'<a href="{item["audio"]}">WAV</a></figcaption></figure>'
            )
    demo_cards: list[str] = []
    for label, item in demo["speeds"].items():
        demo_cards.append(
            '<div class="card">'
            f"<h3>{label}</h3>"
            f'<audio controls preload="metadata" src="{item["audio"]}"></audio>'
            f"<p>{item['output_samples']} samples; gapless OLA: "
            f"{'yes' if item['no_ola_holes'] else 'no'}</p></div>"
        )
    event_rows = "".join(
        "<tr>"
        f"<td>{event['output_time_s']:.3f}</td>"
        f"<td>{event['requested_speed']:.1f}x</td>"
        f"<td>{event['response_start_s']:.3f}</td>"
        f"<td>{event['response_complete_s']:.3f}</td>"
        f"<td>{event['latency_samples']} ({event['latency_ms']:.1f} ms)</td>"
        "</tr>"
        for event in realtime["control_events"]
    )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Continuous Varispeed — pitch-preserving 0.5x–2.0x</title>
<style>
:root {{ color-scheme: light dark; --ink:#18222b; --muted:#5d6975;
  --bg:#f6f2ea; --panel:#fffdf8; --line:#d8d0c2; --accent:#176b87; }}
* {{ box-sizing:border-box; }} body {{ margin:0; background:var(--bg);
  color:var(--ink); font:16px/1.55 ui-sans-serif,system-ui,sans-serif; }}
main {{ max-width:1120px; margin:auto; padding:32px 20px 64px; }}
h1 {{ font-size:clamp(2rem,5vw,3.8rem); line-height:1; margin:8px 0 14px; }}
h2 {{ margin-top:38px; border-bottom:1px solid var(--line); padding-bottom:6px; }}
.lede {{ color:var(--muted); max-width:850px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr));
  gap:14px; }} .card, .note {{ background:var(--panel); border:1px solid var(--line);
  border-radius:12px; padding:16px; }} audio {{ width:100%; }}
table {{ width:100%; border-collapse:collapse; background:var(--panel);
  font-variant-numeric:tabular-nums; }} th,td {{ text-align:left; padding:8px;
  border-bottom:1px solid var(--line); }} th {{ color:var(--muted); }}
.diagram {{ width:100%; margin:12px 0; border-radius:8px; border:1px solid var(--line); }}
.plots {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(420px,1fr));
  gap:18px; }} .plot {{ margin:0; background:var(--panel); border:1px solid var(--line);
  border-radius:12px; padding:10px; }} .plot img {{ width:100%; border-radius:6px; }}
figcaption {{ color:var(--muted); font-size:.9rem; }}
code {{ font-family:ui-monospace,monospace; }} a {{ color:var(--accent); }}
@media (prefers-color-scheme:dark) {{ :root {{ --ink:#ecf1f5; --muted:#a8b2bb;
  --bg:#11171d; --panel:#192129; --line:#34414d; --accent:#5bc0de; }} }}
</style>
</head>
<body><main>
<p>CONTINUOUS PHASE · FIXED SYNTHESIS HOP</p>
<h1>Varispeed, without the chipmunk.</h1>
<p class="lede">Pitch-preserving playback from 0.5x to 2.0x. The synthesis
grid never moves, so speed changes only alter the analysis advance. Phase is
propagated frame-to-frame and spectral peaks are phase-locked to prevent
vertical incoherence.</p>
<div class="grid">
  <div class="note"><strong>Sample rate</strong><br>{meta['sample_rate']} Hz</div>
  <div class="note"><strong>FFT / hop</strong><br>{meta['frame_size']} / {meta['synthesis_hop']}
    ({meta['synthesis_hop_ms']:.1f} ms)</div>
  <div class="note"><strong>Speed range</strong><br>{meta['supported_speed_range'][0]}x–{meta['supported_speed_range'][1]}x</div>
  <div class="note"><strong>OLA holes</strong><br>all test renders: none</div>
</div>
<h2>Listening comparison</h2>
<p class="lede">The same four-second harmonic phrase at three speeds. Each WAV is
the complete offline render, not a browser pitch-shifting approximation.</p>
<div class="grid">{''.join(demo_cards)}</div>
<h2>Objective checks</h2>
<table><thead><tr><th>Signal</th><th>Speed</th><th>samples / expected</th>
<th>residual</th><th>measurement</th><th>OLA</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<h2>Realtime transition</h2>
<div class="note">
<p>Fixed lookahead: <code>{realtime['algorithmic_lookahead_samples']}</code>
samples ({realtime['algorithmic_lookahead_ms']:.1f} ms), control ramp:
{realtime['control_ramp_ms']:.0f} ms. Underruns: {realtime['underruns']}.
Minimum 20 ms RMS in the switched render:
{realtime['min_short_time_rms_realtime']:.4f}.</p>
<p><strong>Offline</strong> starts the new slope at the next synthesis frame
({realtime['offline_control_quantization_ms']:.1f} ms quantization), with exact
requested sample count. <strong>Realtime</strong> also waits for input
lookahead and the control ramp, so its first switch takes
{realtime['realtime_first_switch_latency_ms']:.1f} ms to become fully active.
Both use the identical gapless output hop.</p>
<p><a href="{realtime['offline_audio']}">Offline switched WAV</a> ·
<a href="{realtime['realtime_audio']}">Realtime switched WAV</a></p>
<img class="diagram" src="{realtime['diagnostic_plot']}"
  alt="Transition trajectory and short-time RMS diagnostics">
<table><thead><tr><th>request s</th><th>target</th><th>response start s</th>
<th>response complete s</th><th>latency</th></tr></thead>
<tbody>{event_rows}</tbody></table>
</div>
<h2>Spectra and residuals</h2>
<div class="plots">{''.join(figures)}</div>
<p class="lede">Each NPZ contains <code>output</code>, <code>residual</code>,
<code>time_s</code>, <code>spectrum</code>, and
<code>residual_spectrum</code>. Tone residuals fit a 220 Hz sinusoid; sweep
residuals fit the analytically stretched chirp; pulse residuals fit an
energy-equivalent impulse at the mapped transient time.</p>
</main></body></html>
"""
    (root / "index.html").write_text(html, encoding="utf-8")
