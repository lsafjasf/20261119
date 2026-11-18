"""Matplotlib dashboard: candidates, notch centres/Q, loop level and events.

Two entry points:

* :func:`render_scenario_png` renders a completed run to a static PNG and is
  safe on headless machines;
* :func:`launch_interactive` opens a live window with sliders for loop delay,
  feedback gain and resonance frequency so the closed loop can be tuned.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def _spectrum(signal_block: np.ndarray, fs: float) -> tuple[np.ndarray, np.ndarray]:
    n = signal_block.shape[0]
    window = np.hanning(n)
    spectrum = np.fft.rfft(signal_block * window)
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    db = 20.0 * np.log10(np.abs(spectrum) / max(n, 1) + 1e-12)
    return freqs, db


def _event_series(result, kind: str):
    times = [t for t, event in result.events if event.kind == kind]
    freqs = [event.freq for t, event in result.events if event.kind == kind]
    return times, freqs


def render_scenario_png(name: str, controlled, reference, path: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fs = controlled.fs
    fig, axes = plt.subplots(3, 1, figsize=(11, 10), constrained_layout=True)
    fig.suptitle(f"Automatic notch feedback suppressor — {name}", fontsize=13)

    ax = axes[0]
    late_ctrl = controlled.mic[-int(fs) :]
    late_ref = reference.mic[-int(fs) :]
    f_ctrl, s_ctrl = _spectrum(late_ctrl, fs)
    f_ref, s_ref = _spectrum(late_ref, fs)
    ax.plot(f_ref, s_ref, color="#bbbbbb", lw=1.0, label="unsuppressed loop")
    ax.plot(f_ctrl, s_ctrl, color="#1f77b4", lw=1.1, label="suppressed mic")
    for slot in controlled.controller.bank.slots():
        if slot.active:
            ax.axvline(slot.f0, color="#d62728", ls="--", lw=1.2)
            bandwidth = slot.f0 / slot.q
            ax.axvspan(
                slot.f0 - bandwidth / 2,
                slot.f0 + bandwidth / 2,
                color="#d62728",
                alpha=0.12,
            )
            ax.text(
                slot.f0,
                ax.get_ylim()[1] * 0.92,
                f" f0={slot.f0:.0f}\n Q={slot.q:g}",
                color="#d62728",
                fontsize=8,
                va="top",
            )
    ax.set_xlim(0, fs / 2)
    ax.set_ylim(-120, 5)
    ax.set_ylabel("spectrum [dB]")
    ax.set_title("Final-second spectrum with notch centre and -3 dB bandwidth")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(alpha=0.3)

    ax = axes[1]
    ax.plot(
        reference.block_time_s,
        reference.block_rms_db,
        color="#bbbbbb",
        label="unsuppressed",
    )
    ax.plot(
        controlled.block_time_s,
        controlled.block_rms_db,
        color="#1f77b4",
        label="suppressed",
    )
    detect_t = [t for t, dets in controlled.detection_log if dets]
    if detect_t:
        ax.axvline(detect_t[0], color="#2ca02c", ls=":", lw=1.4,
                   label=f"first detect {detect_t[0]:.2f}s")
    for kind, color in (("open", "#d62728"), ("retune", "#ff7f0e"),
                        ("release", "#7f7f7f")):
        times, _ = _event_series(controlled, kind)
        if times:
            ax.vlines(times, -70, -60, color=color, lw=1.0, label=kind)
    ax.set_ylabel("mic RMS [dB]")
    ax.set_xlabel("time [s]")
    ax.set_title("Closed-loop level and suppressor events")
    ax.legend(loc="upper right", fontsize=8, ncol=2)
    ax.grid(alpha=0.3)

    ax = axes[2]
    if controlled.events:
        for kind, marker in (("open", "o"), ("retune", "."), ("release", "x")):
            times, freqs = _event_series(controlled, kind)
            if times:
                ax.scatter(times, freqs, s=28, marker=marker, label=kind)
    candidate_t, candidate_f = [], []
    for t_s, dets in controlled.detection_log:
        for det in dets:
            candidate_t.append(t_s)
            candidate_f.append(det.freq)
    if candidate_t:
        ax.scatter(candidate_t, candidate_f, s=8, color="#2ca02c",
                   alpha=0.4, label="candidate")
    ax.set_xlabel("time [s]")
    ax.set_ylabel("frequency [Hz]")
    ax.set_title("Detected candidates and allocated notch centres")
    if candidate_t or controlled.events:
        ax.legend(loc="best", fontsize=8)
    else:
        ax.text(
            0.5, 0.5, "no howl candidates — no notch allocated",
            transform=ax.transAxes, ha="center", va="center",
            fontsize=11, color="#555555",
        )
    ax.grid(alpha=0.3)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    import matplotlib.pyplot as plt

    plt.close(fig)


def launch_interactive(scenario_name: str = "drifting_howl") -> None:
    """Open a live tuning window (requires a display; falls back to PNG)."""
    import matplotlib

    try:
        import tkinter  # noqa: F401  (slider widgets need a GUI toolkit)
        matplotlib.use("TkAgg", force=True)
        import matplotlib.pyplot as plt
        from matplotlib.widgets import Slider
    except Exception:
        # Headless or no Tk: degrade gracefully to a static render.
        import matplotlib.pyplot as plt

        from afs.scenarios import SCENARIOS, build_simulator

        scenario = SCENARIOS[scenario_name]()
        controlled = build_simulator(scenario).run(enable_suppression=True)
        reference = build_simulator(scenario).run(enable_suppression=False)
        out = f"artifacts/{scenario_name}_interactive_fallback.png"
        render_scenario_png(scenario_name, controlled, reference, out)
        print(
            "Interactive backend unavailable (no display/Tk); "
            f"wrote static dashboard to {out}"
        )
        return

    from afs.scenarios import SCENARIOS, build_simulator

    scenario = SCENARIOS[scenario_name]()
    simulator = build_simulator(scenario)
    result = simulator.run(enable_suppression=True)

    fig, ax = plt.subplots(figsize=(10, 6))
    plt.subplots_adjust(left=0.1, bottom=0.32)
    freqs, spec = _spectrum(result.mic[-int(result.fs) :], result.fs)
    (line,) = ax.plot(freqs, spec, lw=1.1)
    ax.set_xlim(0, result.fs / 2)
    ax.set_ylim(-120, 5)
    ax.set_xlabel("frequency [Hz]")
    ax.set_ylabel("mic spectrum [dB]")
    ax.grid(alpha=0.3)

    ax_gain = plt.axes([0.15, 0.20, 0.65, 0.03])
    ax_delay = plt.axes([0.15, 0.14, 0.65, 0.03])
    ax_res = plt.axes([0.15, 0.08, 0.65, 0.03])
    slider_gain = Slider(ax_gain, "loop gain", 0.7, 1.25,
                         valinit=scenario.plant.loop_gain)
    slider_delay = Slider(ax_delay, "delay [ms]", 2.0, 30.0,
                          valinit=scenario.plant.delay_ms)
    slider_res = Slider(ax_res, "resonance [Hz]", 500.0, 5000.0,
                        valinit=scenario.plant.resonance_freq)

    status = ax.text(0.01, 0.97, "", transform=ax.transAxes, va="top",
                     fontsize=9, family="monospace")

    def redraw(_=None) -> None:
        scenario.plant.loop_gain = slider_gain.val
        scenario.plant.delay_ms = slider_delay.val
        scenario.plant.resonance_freq = slider_res.val
        fresh = build_simulator(scenario).run(enable_suppression=True)
        f_new, s_new = _spectrum(fresh.mic[-int(fresh.fs) :], fresh.fs)
        line.set_data(f_new, s_new)
        slots = fresh.controller.bank.active_slots()
        status.set_text(
            f"notches: {len(slots)}/{scenario.controller.max_notches}\n"
            + "\n".join(
                f"  f0={slot.f0:7.1f} Hz  Q={slot.q:g}" for slot in slots
            )
        )
        fig.canvas.draw_idle()

    for slider in (slider_gain, slider_delay, slider_res):
        slider.on_changed(redraw)
    redraw()
    plt.show()
