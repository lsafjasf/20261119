"""Offline rendering and export of WAV audio plus parameter trajectories."""

from __future__ import annotations

import csv
import json
import wave

import numpy as np

from .engine import VowelSynthesizer
from .params import SynthParams, clamp_params


def render(params_or_traj, duration: float = 2.0, fs: int = 22050,
           block_size: int = 512, seed: int | None = 1234):
    """Render audio offline.

    ``params_or_traj`` is either a single SynthParams (steady vowel) or a list
    of (time_seconds, SynthParams) keyframes that are interpolated block by
    block, producing a vowel transition. Returns (audio, trajectory_rows).
    """
    synth = VowelSynthesizer(fs=fs, block_size=block_size, seed=seed)
    total = int(round(duration * fs))
    keyframes = _normalise_keyframes(params_or_traj)
    outputs = []
    produced = 0
    while produced < total:
        t = produced / fs
        synth.params = clamp_params(_interp_keyframes(keyframes, t))
        _, out = synth.process_block(n=min(block_size, total - produced))
        outputs.append(out)
        produced += out.shape[0]
    audio = np.concatenate(outputs)[:total]
    return audio, synth.trajectory


def _normalise_keyframes(params_or_traj):
    if isinstance(params_or_traj, SynthParams):
        return [(0.0, params_or_traj)]
    keyframes = list(params_or_traj)
    keyframes.sort(key=lambda kv: kv[0])
    return keyframes


def _interp_keyframes(keyframes, t):
    if t <= keyframes[0][0]:
        return keyframes[0][1]
    if t >= keyframes[-1][0]:
        return keyframes[-1][1]
    for (t0, p0), (t1, p1) in zip(keyframes, keyframes[1:]):
        if t0 <= t <= t1:
            frac = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            merged = {}
            for key in SynthParams().__dict__:
                merged[key] = (1 - frac) * getattr(p0, key) + frac * getattr(p1, key)
            return SynthParams(**merged)
    return keyframes[-1][1]


def write_wav(path: str, audio: np.ndarray, fs: int = 22050):
    """Write 16-bit PCM mono WAV using only the standard library."""
    audio = np.clip(audio, -1.0, 1.0)
    pcm = np.round(audio * 32767.0).astype("<i2")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(fs)
        wf.writeframes(pcm.tobytes())
    return path


def write_trajectory_csv(path: str, trajectory: list[dict]):
    if not trajectory:
        return path
    fields = list(trajectory[0].keys())
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in trajectory:
            writer.writerow({k: round(v, 6) if isinstance(v, float) else v
                             for k, v in row.items()})
    return path


def write_trajectory_json(path: str, trajectory: list[dict], meta: dict | None = None):
    payload = {"meta": meta or {}, "trajectory": trajectory}
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)
    return path
