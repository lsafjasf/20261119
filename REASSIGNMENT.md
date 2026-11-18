# Reassigned Spectrogram Analyzer

Estimates more accurate time and frequency positions for the energy cells of a
short-time Fourier transform (STFT) using the **Auger–Flandrin reassignment
operators**, and displays the ordinary spectrogram and the reassigned
spectrogram side by side in the browser. The implementation is dependency-free
JavaScript (an in-house radix-2 FFT), so it runs in the browser and headlessly
in Node without any installed packages.

## Run

```bash
npm test          # 24 headless assertions (FFT, windows, reassignment, metrics, render, UI)
npm run benchmark # quantitative frequency-concentration and onset-location report
npm run serve     # static server, then open http://localhost:8123/
```

## Reassignment math

For every analysis cell `(frame m, bin k)` three FFTs are evaluated over the
windowed segment:

| FFT | Segment |
| --- | --- |
| `X`   | `x[n] · w[n]` |
| `Xd`  | `x[n] · w'[n]`, read with a circular `+k` bin shift (the `e^{+iωₖn}` phase rotation) |
| `Xt`  | `x[n] · (n−c) · w[n]`, with the centered time weight `c = (N−1)/2` |

With `ωₖ = 2πk/N` and frame center `tₘ` (samples):

```
ω̂ = ωₖ − Im(conj(X)·Xd) / |X|²          (rad/sample → Hz)
t̂ = tₘ + Re(conj(X)·Xt) / |X|²          (samples → seconds)
```

Two sign details were verified numerically rather than copied:

- The frequency operator needs the **phase-rotated** window-derivative spectrum.
  Without the `e^{+iωₖn}` rotation a stationary half-bin tone is mis-localized;
  with it, pure tones snap to their exact carrier (error < 1e-4 Hz).
- The time operator uses a **centered** time weight whose mean is zero, which
  flips the classical minus sign into a plus. An impulse is localized to its
  exact sample.

Each kept cell is then rasterized onto the nearest target `(frame, bin)` and
its energy is accumulated there. The reassigned map is the sum of moved energy;
cells that cannot be placed are never silently dropped.

## Cell status and discard accounting

Every cell is tagged with exactly one status, and the total energy is split
across disjoint buckets whose sum equals the input energy (gap = 0):

| Status | Meaning |
| --- | --- |
| `kept` | above the energy floor, finite, and rasterized inside the grid |
| `low-energy` | `energy ≤ max(maxCellEnergy · relFloor, absFloor)` — never divided into, so zero energy cannot cause a divide-by-zero |
| `non-finite` | reassignment produced `NaN`/`Infinity` (defensive) |
| `out-of-bounds-time` | target frame is `< 0` or `≥ frames` (e.g. leading-edge transients) |
| `out-of-bounds-freq` | target bin is `< 0` or outside `[0, Nyquist]` |

Out-of-bounds energy is accumulated separately in `stats.oobEnergy` (with
per-axis cell counts) and is excluded from the reassigned grid. Silence yields
only low-energy cells, no infinities and no exceptions.

## UI behavior

- **Side-by-side panels** share identical time (seconds) and frequency (Hz)
  axes and a shared dB color scale, so the two maps are directly comparable.
- **Window-length control** (256/512/1024/2048) keeps the coordinate units and
  the `0 … Nyquist` range fixed; only bin width (`fs/N`) and frame step
  (`hop/fs`) change. Hop is `N/4`.
- **Click any cell** in either panel to see the original `(time, frequency)`,
  the reassigned `(time, frequency)`, both shifts, the target grid cell, and —
  for discarded cells — the explicit discard reason.

## Quantitative results (fs = 16 kHz)

`npm run benchmark` sweeps the three mandated signals across window lengths.
Representative findings:

- **Nearby pure tones (2000/2250 Hz):** reassigned peak-cell carrier error is
  below 0.001 Hz; cells above 1% of the map peak drop to roughly a third of the
  STFT count and the strongest cell gains ~1.5× energy.
- **Crossing chirps (1–4 kHz):** peak-cell instantaneous-frequency error is no
  worse than the grid at every window length, a larger fraction of energy falls
  inside a half-bin band, and occupied cells above 1% peak fall by more than
  half at long windows (e.g. 1135 → 491 at N = 1024).
- **Transient burst (3 kHz, σ = 4 ms):** the energy-weighted time bias stays
  near zero while the time spread collapses as the window grows — at N = 2048,
  from ~18.5 ms (STFT) to ~0.45 ms (reassigned), quantifying the onset
  localization gain.

Note on interpretation: for a perfectly stationary, bin-aligned tone the
reassigned coordinates coincide with the grid (the local instantaneous
frequency is constant), so reassignment cannot improve an already-exact cell.
Its measurable benefits are sub-bin carrier accuracy for off-bin tones,
focused rasterization (fewer, stronger cells), and sharp time localization of
transients — all reported by the benchmark rather than assumed.

## Layout

```
js/fft.js          radix-2 FFT
js/windows.js      Hann windows, discrete derivative, centered time weight
js/signals.js      crossing chirps, close pure tones, transient burst
js/reassigned.js   Auger-Flandrin reassignment + status/energy accounting
js/metrics.js      concentration, peak error, onset error, energy accounting
js/render.js       canvas heat-map views with shared physical axes
js/app.js          controller: controls, side-by-side render, cell inspector
test/              zero-dependency unit tests, benchmark and static server
```
