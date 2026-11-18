// UI controller: signal selection, window-length control with fixed physical
// axes, side-by-side rendering and the clickable cell inspector.

import { reassignedSpectrogram, CellStatus, STATUS_LABEL } from './reassigned.js';
import { SIGNAL_BUILDERS } from './signals.js';
import { SpectrogramView } from './render.js';
import {
  toneConcentration,
  onsetLocalization,
  peakCellFrequencyError,
  rasterConcentration,
  energyAccounting
} from './metrics.js';

const WINDOW_CHOICES = [256, 512, 1024, 2048];

export class AnalyzerApp {
  constructor() {
    this.signalKey = 'crossing-chirps';
    this.nfft = 1024;
    this.sampleRate = 16000;
    this.result = null;
    this.stftView = new SpectrogramView(document.getElementById('stft-canvas'), {
      onClick: (cell) => this.inspect(cell)
    });
    this.reView = new SpectrogramView(document.getElementById('reassigned-canvas'), {
      onClick: (cell) => this.inspect(cell)
    });
    this._bindControls();
    this.recompute();
    window.addEventListener('resize', () => this.render());
  }

  _bindControls() {
    const signalSelect = document.getElementById('signal-select');
    signalSelect.addEventListener('change', () => {
      this.signalKey = signalSelect.value;
      this.recompute();
    });

    const windowSelect = document.getElementById('window-select');
    for (const n of WINDOW_CHOICES) {
      const opt = document.createElement('option');
      opt.value = String(n);
      opt.textContent = `${n} samples (${(n / this.sampleRate * 1000).toFixed(1)} ms)`;
      windowSelect.appendChild(opt);
    }
    windowSelect.value = String(this.nfft);
    windowSelect.addEventListener('change', () => {
      this.nfft = Number(windowSelect.value);
      this.recompute();
    });

    document.getElementById('recompute').addEventListener('click', () => this.recompute());
  }

  recompute() {
    const builder = SIGNAL_BUILDERS[this.signalKey];
    const built = builder({ sampleRate: this.sampleRate });
    const hop = Math.max(1, Math.floor(this.nfft / 4));
    const t0 = performance.now();
    this.result = reassignedSpectrogram(built.samples, {
      nfft: this.nfft,
      hop,
      sampleRate: this.sampleRate
    });
    this.computeMs = performance.now() - t0;
    this.signalMeta = built.meta;
    this.render();
    this._renderStats(built);
  }

  render() {
    if (!this.result) return;
    this.stftView.setResult(this.result, 'spectrogram', 'Ordinary STFT |E|²');
    this.reView.setResult(this.result, 'reassignedGrid', 'Reassigned spectrogram');
  }

  inspect(cell) {
    const { frame, bin } = cell;
    const idx = frame * this.result.params.halfBins + bin;
    const r = this.result;
    const status = r.status[idx];
    const rows = [
      ['Cell', `frame ${frame}, bin ${bin}`],
      ['Status', STATUS_LABEL[status]],
      ['Original time', `${r.origTime[idx].toFixed(5)} s`],
      ['Original frequency', `${r.origFreq[idx].toFixed(2)} Hz`],
      ['Energy', r.energy[idx].toExponential(4)]
    ];
    if (status === CellStatus.KEPT || status === CellStatus.OUT_OF_BOUNDS_TIME ||
        status === CellStatus.OUT_OF_BOUNDS_FREQ) {
      rows.push(
        ['Reassigned time', `${r.reTime[idx].toFixed(5)} s`],
        ['Reassigned frequency', `${r.reFreq[idx].toFixed(2)} Hz`],
        ['Time shift', `${((r.reTime[idx] - r.origTime[idx]) * 1000).toFixed(3)} ms`],
        ['Frequency shift', `${(r.reFreq[idx] - r.origFreq[idx]).toFixed(2)} Hz`],
        ['Target grid cell', `frame ${r.targetFrame[idx]}, bin ${r.targetBin[idx]}`]
      );
    } else {
      rows.push(['Reassignment', 'not computed (cell discarded)']);
    }
    if (status === CellStatus.LOW_ENERGY) {
      rows.push(['Discard reason', `energy ${r.energy[idx].toExponential(2)} below floor ${r.stats.energyFloor.toExponential(2)}`]);
    } else if (status === CellStatus.NON_FINITE) {
      rows.push(['Discard reason', 'reassigned coordinates were NaN/Infinity']);
    } else if (status === CellStatus.OUT_OF_BOUNDS_TIME) {
      rows.push(['Discard reason', 'reassigned time maps outside the frame range']);
    } else if (status === CellStatus.OUT_OF_BOUNDS_FREQ) {
      rows.push(['Discard reason', 'reassigned frequency outside [0, Nyquist]']);
    }

    const panel = document.getElementById('inspector');
    panel.innerHTML = rows
      .map(([k, v]) => `<div class="ins-row"><span class="ins-key">${k}</span><span class="ins-val">${v}</span></div>`)
      .join('');
    panel.classList.add('active');
  }

  _renderStats(built) {
    const r = this.result;
    const s = r.stats;
    const accounting = energyAccounting(r);
    let metricHtml = '';
    if (this.signalKey === 'close-pure-tones') {
      const peak = peakCellFrequencyError(r, built.meta.tones.map((t) => () => t.f));
      metricHtml = `
        <li>Peak-cell carrier error: grid ${peak.stftPeakErrorHz.toFixed(2)} Hz,
            reassigned ${peak.reassignedPeakErrorHz.toFixed(4)} Hz</li>`;
    } else if (this.signalKey === 'transient-burst') {
      const loc = onsetLocalization(r, built.meta.centerSec);
      metricHtml = `
        <li>Time bias: grid ${(loc.biasStft * 1000).toFixed(3)} ms,
            reassigned ${(loc.biasReassigned * 1000).toFixed(4)} ms</li>
        <li>Time spread: grid ${(loc.spreadStftSec * 1000).toFixed(3)} ms,
            reassigned ${(loc.spreadReassignedSec * 1000).toFixed(3)} ms</li>`;
    } else {
      const peak = peakCellFrequencyError(r, [
        (t) => built.meta.instantFreqUp(t),
        (t) => built.meta.instantFreqDown(t)
      ]);
      metricHtml = `
        <li>Peak-cell IF error: grid ${peak.stftPeakErrorHz.toFixed(2)} Hz,
            reassigned ${peak.reassignedPeakErrorHz.toFixed(3)} Hz</li>`;
    }
    const conc = rasterConcentration(r);
    const occ1 = conc.occupied.find((o) => o.fraction === 0.01);

    document.getElementById('stats').innerHTML = `
      <div class="stat-grid">
        <div><b>${r.params.nfft}</b><span>FFT length</span></div>
        <div><b>${r.params.hop}</b><span>hop (samples)</span></div>
        <div><b>${(r.params.nfft / r.params.sampleRate * 1000).toFixed(1)} ms</b><span>window duration</span></div>
        <div><b>${this.computeMs.toFixed(1)} ms</b><span>compute time</span></div>
      </div>
      <ul class="metric-list">
        ${metricHtml}
        <li>Occupied cells &gt;1% peak: grid ${occ1.stftCells}, reassigned ${occ1.reassignedCells}
            (${(occ1.reductionRatio * 100).toFixed(0)}% as many)</li>
        <li>Peak energy gain: ${conc.peakGain.toFixed(2)}x</li>
      </ul>
      <h4>Energy accounting</h4>
      <table class="acc-table">
        <tr><td>Total energy</td><td>${s.totalEnergy.toExponential(4)}</td></tr>
        <tr><td>Kept cells</td><td>${s.keptCells} (${s.keptEnergy.toExponential(3)})</td></tr>
        <tr><td>Low-energy discarded</td><td>${s.lowEnergyCells} (${s.lowEnergyEnergy.toExponential(2)})</td></tr>
        <tr><td>Non-finite discarded</td><td>${s.nonFiniteCells}</td></tr>
        <tr><td>Out-of-bounds time</td><td>${s.oobTimeCells}</td></tr>
        <tr><td>Out-of-bounds frequency</td><td>${s.oobFreqCells}</td></tr>
        <tr><td>OOB energy (separate)</td><td>${s.oobEnergy.toExponential(4)} (${(accounting.oobFraction * 100).toFixed(4)}%)</td></tr>
        <tr><td>Accounting gap</td><td>${accounting.absoluteGap.toExponential(1)}</td></tr>
        <tr><td>Energy floor</td><td>${s.energyFloor.toExponential(3)}</td></tr>
      </table>`;
  }
}

if (typeof document !== 'undefined' && typeof window !== 'undefined' &&
    typeof window.addEventListener === 'function') {
  window.addEventListener('DOMContentLoaded', () => {
    window.app = new AnalyzerApp();
  });
}
