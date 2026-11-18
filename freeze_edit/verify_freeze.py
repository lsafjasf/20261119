#!/usr/bin/env python3
"""Verify a freeze-frame insertion against the labelled source material.

Checks performed
----------------
1. Every output frame has a unique, monotonically increasing PTS at the source
   frame cadence (no gaps, no duplicates, no resampling drops).
2. The frozen region shows exactly the requested source frame number, held for
   exactly the requested number of frames.
3. Frames after the freeze are the expected source frames, shifted later by
   exactly the hold duration.
4. Audio matches the declared strategy:
   - continue: audio duration equals the source duration.
   - mute:     silence throughout the freeze window, original audio elsewhere.
   - extend:   non-silent held audio throughout the freeze window.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True)


def probe(path: Path, ffprobe: str) -> dict:
    proc = run([
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    if proc.returncode:
        raise RuntimeError(proc.stderr.decode())
    return json.loads(proc.stdout)


def stream_of(info: dict, kind: str) -> dict | None:
    for stream in info["streams"]:
        if stream["codec_type"] == kind:
            return stream
    return None


def decode_frames(path: Path, width: int, height: int, ffmpeg: str) -> np.ndarray:
    proc = subprocess.Popen(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        stdout=subprocess.PIPE,
    )
    frame_size = width * height * 3
    frames = []
    assert proc.stdout is not None
    while True:
        buf = proc.stdout.read(frame_size)
        if len(buf) < frame_size:
            break
        frames.append(np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3))
    proc.wait()
    return np.stack(frames)


def read_pts(path: Path, ffprobe: str) -> list[float]:
    proc = run([
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(path),
    ])
    values = []
    for line in proc.stdout.decode().splitlines():
        token = line.strip().rstrip(",").strip()
        if token:
            values.append(float(token))
    return values


def frame_signature(frame: np.ndarray) -> np.ndarray:
    # Downsampled signature: robust to codec noise, identical for repeated frames.
    small = Image.fromarray(frame).resize((32, 18), Image.Resampling.BILINEAR)
    return np.asarray(small, dtype=np.int16).reshape(-1)


def classify_frames(frames: np.ndarray, src_frames: np.ndarray) -> list[int]:
    """Map each output frame to its source frame number via nearest signature."""
    src_sigs = np.stack([frame_signature(f) for f in src_frames])
    mapping = []
    for frame in frames:
        sig = frame_signature(frame)
        dist = np.abs(src_sigs.astype(np.int16) - sig).sum(axis=1)
        mapping.append(int(np.argmin(dist)))
    return mapping


def extract_audio(path: Path, ffmpeg: str, wav_path: Path) -> tuple[np.ndarray, int]:
    proc = run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(path), "-ac", "1", "-vn", str(wav_path),
    ])
    if proc.returncode:
        raise RuntimeError(proc.stderr.decode())
    with wave.open(str(wav_path), "rb") as wav:
        rate = wav.getframerate()
        pcm = wav.readframes(wav.getnframes())
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float64) / 32768.0, rate


def rms(segment: np.ndarray) -> float:
    return float(np.sqrt(np.mean(segment ** 2))) if segment.size else 0.0


def verify(
    output: Path, source: Path, spec: dict, ffmpeg: str, ffprobe: str, work: Path
) -> tuple[bool, list[str]]:
    log: list[str] = []
    ok = True

    def check(cond: bool, message: str) -> None:
        nonlocal ok
        log.append(("PASS" if cond else "FAIL") + " - " + message)
        ok = ok and cond

    out_info = probe(output, ffprobe)
    src_info = probe(source, ffprobe)
    v_out = stream_of(out_info, "video")
    v_src = stream_of(src_info, "video")
    width = int(v_out["width"])
    height = int(v_out["height"])
    fps_num, fps_den = map(int, v_out["r_frame_rate"].split("/"))
    fps = fps_num / fps_den

    frames = decode_frames(output, width, height, ffmpeg)
    src_frames = decode_frames(source, width, height, ffmpeg)
    mapping = classify_frames(frames, src_frames)

    freeze_frame = round(spec["freeze_at"] * fps)
    hold_frames = round(spec["actual_hold_duration"] * fps)
    freeze_start = freeze_frame
    freeze_end = freeze_start + hold_frames  # exclusive

    # 1. PTS continuity.
    pts = read_pts(output, ffprobe)
    check(len(pts) == len(frames),
          f"frame count: decoded={len(frames)} pts_entries={len(pts)}")
    deltas = np.diff(pts)
    check(np.allclose(deltas, 1.0 / fps, atol=1e-4),
          f"PTS strictly increments at 1/{fps_num}s cadence "
          f"(min={deltas.min():.6f}, max={deltas.max():.6f})")
    check(abs(pts[-1] - (len(pts) - 1) / fps) < 1e-3,
          f"last PTS {pts[-1]:.3f}s matches (N-1)/fps")

    # 2. Frozen region content and length.
    frozen_mapping = mapping[freeze_start:freeze_end]
    check(all(m == freeze_frame for m in frozen_mapping),
          f"freeze region frames {freeze_start}..{freeze_end - 1} all show "
          f"source frame {freeze_frame} (unique seen: {sorted(set(frozen_mapping))})")
    check(len(frozen_mapping) == hold_frames,
          f"freeze length = {hold_frames} frames "
          f"({hold_frames / fps:.3f}s)")

    # 3. Pre/post content and downstream shift.
    pre_ok = mapping[:freeze_start] == list(range(freeze_start))
    check(bool(pre_ok),
          f"pre-freeze frames 0..{freeze_start - 1} match source 1:1")
    expected_post = list(range(freeze_frame, len(src_frames)))
    actual_post = mapping[freeze_end:]
    check(actual_post == expected_post,
          f"post-freeze output frame {freeze_end} resumes at source frame "
          f"{freeze_frame}; all {len(expected_post)} downstream frames match "
          f"(shift = {hold_frames / fps:.3f}s)")

    # 4. Audio strategy.
    a_out = stream_of(out_info, "audio")
    a_src = stream_of(src_info, "audio")
    if a_src is None:
        check(a_out is None, "source has no audio; output has none either")
        return ok, log

    audio, rate = extract_audio(output, ffmpeg, work / "verify_out.wav")
    src_audio, _ = extract_audio(source, ffmpeg, work / "verify_src.wav")
    audio_dur = len(audio) / rate
    src_dur = len(src_audio) / rate
    win0 = int(freeze_frame / fps * rate)
    win1 = int(freeze_end / fps * rate)
    freeze_rms = rms(audio[win0:win1])

    if spec["audio"] == "continue":
        check(abs(audio_dur - src_dur) < 0.05,
              f"continue: audio duration {audio_dur:.3f}s == source {src_dur:.3f}s")
        check(freeze_rms > 0.01,
              f"continue: audio keeps playing during freeze (rms={freeze_rms:.4f})")
    elif spec["audio"] == "mute":
        check(abs(audio_dur - (src_dur + hold_frames / fps)) < 0.05,
              f"mute: audio grew by freeze to {audio_dur:.3f}s "
              f"(expected {src_dur + hold_frames / fps:.3f}s)")
        check(freeze_rms < 1e-4,
              f"mute: silence during freeze window (rms={freeze_rms:.2e})")
        pre_rms = rms(audio[: win0 - rate // 10])
        post_rms = rms(audio[win1 + rate // 10:])
        check(pre_rms > 0.01 and post_rms > 0.01,
              f"mute: original audio present before/after freeze "
              f"(rms {pre_rms:.4f}/{post_rms:.4f})")
    else:  # extend
        check(abs(audio_dur - (src_dur + hold_frames / fps)) < 0.05,
              f"extend: audio grew by freeze to {audio_dur:.3f}s")
        check(freeze_rms > 0.01,
              f"extend: held sound fills freeze window (rms={freeze_rms:.4f})")

    return ok, log


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("output", type=Path)
    ap.add_argument("source", type=Path)
    ap.add_argument("--spec", type=Path, required=True,
                    help="sidecar JSON written by freeze_insert.py")
    ap.add_argument("--work", type=Path, default=Path("work"))
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--ffprobe", default="ffprobe")
    args = ap.parse_args()

    spec = json.loads(args.spec.read_text())
    args.work.mkdir(parents=True, exist_ok=True)
    ok, log = verify(args.output, args.source, spec,
                     args.ffmpeg, args.ffprobe, args.work)
    print("\n".join(log))
    print(f"verify {args.output.name}: {'ALL PASS' if ok else 'FAILURES PRESENT'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
