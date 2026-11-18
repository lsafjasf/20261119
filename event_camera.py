"""Event camera (DVS) simulator for image sequences.

Each pixel emits an event (x, y, t, polarity) whenever its log brightness
crosses a multiple of the contrast threshold relative to the log brightness
at the pixel's last event. Between frames, log brightness is interpolated
linearly in time, so sub-frame trigger timestamps are computed analytically.

Supported non-idealities:
  - per-pixel contrast-threshold mismatch (log-normal spread)
  - per-pixel refractory period
  - hot pixels (spurious Poisson events)

Events are stored as a structured NumPy array and can be saved to / loaded
from ``.npz`` or ``.txt`` files.
"""

from __future__ import annotations

import numpy as np

EVENT_DTYPE = np.dtype(
    [("t", "<f8"), ("x", "<u2"), ("y", "<u2"), ("p", "<i1")]
)

EPS = 1e-6  # lower clamp for intensities before taking the log


def make_events(t, x, y, p) -> np.ndarray:
    """Pack parallel arrays into the structured event array."""
    n = len(t)
    ev = np.empty(n, dtype=EVENT_DTYPE)
    ev["t"] = t
    ev["x"] = x
    ev["y"] = y
    ev["p"] = p
    return ev


def save_events(path: str, events: np.ndarray) -> None:
    """Save events to ``.npz`` (binary) or ``.txt`` (``t x y p`` per line)."""
    events = np.sort(events, order="t") if events.size else events
    if str(path).endswith(".npz"):
        np.savez(path, t=events["t"], x=events["x"], y=events["y"], p=events["p"])
    else:
        data = np.column_stack([events["t"], events["x"], events["y"], events["p"]])
        np.savetxt(path, data, fmt="%.9f %d %d %d")


def load_events(path: str) -> np.ndarray:
    """Load events previously written by :func:`save_events`."""
    if str(path).endswith(".npz"):
        with np.load(path) as z:
            return make_events(z["t"], z["x"], z["y"], z["p"])
    data = np.loadtxt(path, ndmin=2)
    if data.size == 0:
        return np.empty(0, dtype=EVENT_DTYPE)
    return make_events(data[:, 0], data[:, 1], data[:, 2], data[:, 3])


class EventCameraSimulator:
    """Per-pixel log-brightness threshold-crossing event simulator.

    Parameters
    ----------
    width, height : sensor resolution in pixels.
    C_pos, C_off : nominal ON/OFF contrast thresholds (log-intensity units).
    threshold_sigma : std-dev of the per-pixel threshold mismatch, applied
        as a log-normal multiplier on both thresholds (0 disables mismatch).
    refractory_period : minimum time (seconds) between two events of the
        same pixel; events falling inside the dead time are dropped while
        the reference level still advances (0 disables).
    hot_pixel_rate : mean spurious events per second for hot pixels
        (0 disables).
    hot_pixel_fraction : fraction of pixels that are "hot".
    seed : RNG seed for reproducibility.
    """

    def __init__(
        self,
        width: int,
        height: int,
        C_pos: float = 0.2,
        C_off: float = 0.2,
        threshold_sigma: float = 0.0,
        refractory_period: float = 0.0,
        hot_pixel_rate: float = 0.0,
        hot_pixel_fraction: float = 0.01,
        seed: int = 0,
    ):
        self.width = int(width)
        self.height = int(height)
        self.C_pos = float(C_pos)
        self.C_off = float(C_off)
        self.refractory_period = float(refractory_period)
        self.hot_pixel_rate = float(hot_pixel_rate)
        self.rng = np.random.default_rng(seed)

        shape = (self.height, self.width)
        if threshold_sigma > 0:
            mismatch = np.exp(
                self.rng.normal(0.0, threshold_sigma, size=(2,) + shape)
            )
            self.th_pos = self.C_pos * mismatch[0]
            self.th_off = self.C_off * mismatch[1]
        else:
            self.th_pos = np.full(shape, self.C_pos)
            self.th_off = np.full(shape, self.C_off)

        n_hot = int(round(hot_pixel_fraction * self.width * self.height))
        self.hot_pixels = self.rng.choice(
            self.width * self.height, size=n_hot, replace=False
        )

        # Sensor state.
        self._log_ref = np.zeros(shape)      # log brightness at last event
        self._log_prev = None                # log brightness of last frame
        self._t_prev = None                  # timestamp of last frame
        self._last_event_t = np.full(shape, -np.inf)  # for refractory time
        self._initialised = False

    def reset(self) -> None:
        self._log_ref.fill(0.0)
        self._log_prev = None
        self._t_prev = None
        self._last_event_t.fill(-np.inf)
        self._initialised = False

    @staticmethod
    def _to_log(image: np.ndarray) -> np.ndarray:
        img = np.asarray(image, dtype=np.float64)
        if img.max(initial=0.0) > 1.0 + 1e-9:
            img = img / 255.0
        return np.log(np.clip(img, EPS, None))

    def process_frame(self, image: np.ndarray, t: float) -> np.ndarray:
        """Feed one frame; return events emitted in (t_prev, t].

        The first frame only initialises the reference brightness and
        produces no events. Log brightness is interpolated linearly between
        consecutive frames: L(s) = L0 + (L1 - L0) * (s - t0) / dt, so the
        k-th crossing of level ``ref + k*C`` happens at
        ``s = t0 + dt * (ref + k*C - L0) / (L1 - L0)``.
        """
        t = float(t)
        log_img = self._to_log(image)
        if log_img.shape != (self.height, self.width):
            raise ValueError(
                f"frame shape {log_img.shape} != sensor "
                f"({self.height}, {self.width})"
            )
        if not self._initialised:
            self._log_ref = log_img.copy()
            self._log_prev = log_img
            self._t_prev = t
            self._initialised = True
            return np.empty(0, dtype=EVENT_DTYPE)

        t0, t1 = self._t_prev, t
        if t1 <= t0:
            raise ValueError("frame timestamps must be strictly increasing")
        L0, L1 = self._log_prev, log_img
        dt = t1 - t0

        chunks = [self._threshold_events(L0, L1, t0, t1, +1),
                  self._threshold_events(L0, L1, t0, t1, -1)]
        if self.hot_pixel_rate > 0 and self.hot_pixels.size:
            chunks.append(self._hot_pixel_events(t0, t1))

        self._log_prev = L1
        self._t_prev = t1
        if not chunks:
            return np.empty(0, dtype=EVENT_DTYPE)
        events = np.concatenate(chunks)
        return np.sort(events, order="t")

    def _threshold_events(self, L0, L1, t0, t1, sign) -> np.ndarray:
        """Events of one polarity for the frame interval (vectorised over
        the crossing index k = 1..max)."""
        th = self.th_pos if sign > 0 else self.th_off
        delta = (L1 - self._log_ref) * sign
        n_cross = np.floor(delta / th).astype(np.int64)
        n_cross[delta <= 0] = 0
        if n_cross.max() == 0:
            return np.empty(0, dtype=EVENT_DTYPE)

        dL = L1 - L0
        dt = t1 - t0
        ts, xs, ys = [], [], []
        emitted_ref = self._log_ref.copy()
        for k in range(1, n_cross.max() + 1):
            mask = n_cross >= k
            if not mask.any():
                break
            level = self._log_ref[mask] + k * th[mask] * sign
            denom = dL[mask]
            # Crossing time by linear log-brightness interpolation. Where
            # brightness is constant (denom == 0) the crossing is backlog
            # from a previous interval: emit at the interval start.
            frac = np.where(np.abs(denom) > 1e-12,
                            (level - L0[mask]) / np.where(
                                np.abs(denom) > 1e-12, denom, 1.0),
                            0.0)
            t_k = t0 + dt * np.clip(frac, 0.0, 1.0)
            if self.refractory_period > 0:
                ok = (t_k - self._last_event_t[mask]
                      >= self.refractory_period - 1e-12)
            else:
                ok = np.ones(mask.sum(), dtype=bool)
            if not ok.any():
                continue
            idx = np.flatnonzero(mask)[ok]
            yy, xx = np.divmod(idx, self.width)
            ts.append(t_k[ok])
            xs.append(xx)
            ys.append(yy)
            self._last_event_t[yy, xx] = t_k[ok]
            emitted_ref[yy, xx] = level[ok]

        # The reference level advances only with *emitted* events; blocked
        # crossings stay pending and drain over later intervals.
        self._log_ref = emitted_ref
        if not ts:
            return np.empty(0, dtype=EVENT_DTYPE)
        n = sum(len(a) for a in ts)
        return make_events(
            np.concatenate(ts), np.concatenate(xs), np.concatenate(ys),
            np.full(n, sign, dtype=np.int8),
        )

    def _hot_pixel_events(self, t0: float, t1: float) -> np.ndarray:
        dt = t1 - t0
        counts = self.rng.poisson(self.hot_pixel_rate * dt,
                                  size=self.hot_pixels.size)
        total = counts.sum()
        if total == 0:
            return np.empty(0, dtype=EVENT_DTYPE)
        flat = np.repeat(self.hot_pixels, counts)
        yy, xx = np.divmod(flat, self.width)
        tt = t0 + self.rng.random(total) * dt
        pp = self.rng.choice(np.array([-1, 1], dtype=np.int8), size=total)
        return make_events(tt, xx, yy, pp)

    def run(self, frames, timestamps) -> np.ndarray:
        """Process a whole sequence; return all events sorted by time."""
        chunks = [self.process_frame(f, t)
                  for f, t in zip(frames, timestamps)]
        chunks = [c for c in chunks if c.size]
        if not chunks:
            return np.empty(0, dtype=EVENT_DTYPE)
        return np.sort(np.concatenate(chunks), order="t")


def time_surface(events: np.ndarray, width: int, height: int, t: float,
                 tau: float, polarity: int | None = None) -> np.ndarray:
    """Exponential time surface at time ``t`` (SAE decay kernel).

    Returns an (height, width) array with ``exp(-(t - t_last) / tau)`` per
    pixel, where ``t_last`` is the timestamp of the most recent event (of
    the requested polarity) at that pixel. ``polarity`` may be +1, -1 or
    None (both).
    """
    if polarity is not None:
        events = events[events["p"] == polarity]
    last_t = np.full((height, width), -np.inf)
    if events.size:
        order = np.argsort(events["t"], kind="stable")
        ev = events[order]
        last_t[ev["y"], ev["x"]] = ev["t"]
    surf = np.zeros((height, width))
    valid = np.isfinite(last_t) & (last_t <= t)
    surf[valid] = np.exp(-(t - last_t[valid]) / tau)
    return surf


def render_events(events: np.ndarray, width: int, height: int,
                  bg=(255, 255, 255), on=(220, 30, 30),
                  off=(30, 60, 220)) -> np.ndarray:
    """Render events as an RGB image (ON=red, OFF=blue by default)."""
    img = np.empty((height, width, 3), dtype=np.uint8)
    img[:] = bg
    if events.size:
        on_mask = events["p"] > 0
        img[events["y"][on_mask], events["x"][on_mask]] = on
        img[events["y"][~on_mask], events["x"][~on_mask]] = off
    return img
