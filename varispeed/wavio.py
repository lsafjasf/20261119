"""Small WAV helpers shared by the CLI and report generator."""

from __future__ import annotations

from pathlib import Path

import numpy as np


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    try:
        import soundfile as sf
    except ImportError as exc:  # pragma: no cover - dependency message
        raise RuntimeError("soundfile is required to read WAV files") from exc
    data, sample_rate = sf.read(str(path), always_2d=False, dtype="float64")
    return np.asarray(data), int(sample_rate)


def write_wav(
    path: str | Path,
    audio: np.ndarray,
    sample_rate: int,
    *,
    subtype: str = "PCM_24",
) -> Path:
    try:
        import soundfile as sf
    except ImportError as exc:  # pragma: no cover - dependency message
        raise RuntimeError("soundfile is required to write WAV files") from exc
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = np.asarray(audio, dtype=np.float64)
    data = np.clip(data, -1.0, 1.0)
    sf.write(str(destination), data, int(sample_rate), subtype=subtype)
    return destination
