"""Demo: simulate a moving bright/dark edge, save the event file, and
render the event stream and time surfaces into ``artifacts/``.

Run:  python3 demo_moving_edge.py
"""

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from event_camera import (
    EventCameraSimulator,
    load_events,
    render_events,
    save_events,
    time_surface,
)

W, H = 128, 96
FPS = 240.0
SPEED = 60.0  # px/s
OUT = "artifacts"


def make_sequence():
    """Bright bar sweeping right, then a dark bar sweeping right."""
    n = 200
    ts = np.arange(n) / FPS
    frames = []
    for t in ts:
        img = np.full((H, W), 0.15)
        edge = 10 + SPEED * t
        img[:, np.arange(W) < edge] = 0.85
        # Dark bar chasing the bright one (produces OFF events).
        edge2 = 10 + SPEED * (t - 0.35)
        if edge2 > 0:
            img[:, np.arange(W) < edge2] = 0.15
        frames.append(img)
    return frames, ts


def main():
    os.makedirs(OUT, exist_ok=True)
    frames, ts = make_sequence()

    sim = EventCameraSimulator(
        W, H,
        C_pos=0.2, C_off=0.2,
        threshold_sigma=0.15,
        refractory_period=1e-3,
        hot_pixel_rate=20.0,
        hot_pixel_fraction=0.005,
        seed=42,
    )
    events = sim.run(frames, ts)
    print(f"generated {events.size} events "
          f"({np.sum(events['p'] > 0)} ON, {np.sum(events['p'] < 0)} OFF)")

    # Event file (binary + text) and re-read check.
    save_events(f"{OUT}/events.npz", events)
    save_events(f"{OUT}/events.txt", events)
    reloaded = load_events(f"{OUT}/events.npz")
    assert reloaded.shape == events.shape
    print(f"events saved to {OUT}/events.npz and {OUT}/events.txt "
          f"(re-read OK, {reloaded.size} events)")

    # --- Figure 1: sample frame + accumulated event stream + time surface.
    t_show = 0.55
    window = (events["t"] >= t_show - 0.05) & (events["t"] <= t_show)
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    axes[0].imshow(frames[min(int(t_show * FPS), len(frames) - 1)],
                   cmap="gray", vmin=0, vmax=1)
    axes[0].set_title(f"input frame @ {t_show:.3f}s")
    axes[1].imshow(render_events(events[window], W, H))
    axes[1].set_title("event stream (50 ms window)\nred=ON blue=OFF")
    surf = time_surface(events, W, H, t_show, tau=0.02)
    im = axes[2].imshow(surf, cmap="inferno", vmin=0, vmax=1)
    axes[2].set_title(f"time surface (tau=20ms) @ {t_show:.3f}s")
    fig.colorbar(im, ax=axes[2], fraction=0.046)
    for ax in axes:
        ax.set_xticks([])
        ax.set_yticks([])
    fig.tight_layout()
    fig.savefig(f"{OUT}/event_stream_and_time_surface.png", dpi=130)
    plt.close(fig)

    # --- Figure 2: per-polarity time surfaces + event rate over time.
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    for ax, pol, title in ((axes[0], 1, "ON time surface"),
                           (axes[1], -1, "OFF time surface")):
        s = time_surface(events, W, H, t_show, tau=0.02, polarity=pol)
        ax.imshow(s, cmap="inferno", vmin=0, vmax=1)
        ax.set_title(title)
        ax.set_xticks([])
        ax.set_yticks([])
    bins = np.arange(0, ts[-1] + 1 / FPS, 1 / FPS)
    axes[2].hist(events[events["p"] > 0]["t"], bins=bins, color="red",
                 alpha=0.7, label="ON")
    axes[2].hist(events[events["p"] < 0]["t"], bins=bins, color="blue",
                 alpha=0.7, label="OFF")
    axes[2].set_title("event rate")
    axes[2].set_xlabel("t [s]")
    axes[2].legend()
    fig.tight_layout()
    fig.savefig(f"{OUT}/polarity_surfaces_and_rate.png", dpi=130)
    plt.close(fig)
    print(f"figures written to {OUT}/")


if __name__ == "__main__":
    main()
