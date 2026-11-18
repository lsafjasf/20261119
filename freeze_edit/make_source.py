#!/usr/bin/env python3
"""Generate a test source whose frames are visibly labelled with frame numbers.

The video also carries a distinctive tone so that audio strategies can be
verified independently of the picture.
"""

from __future__ import annotations

import argparse
import math
import struct
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def pick_font(size: int) -> ImageFont.FreeTypeFont:
    for name in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def render_frame(frame_num: int, width: int, height: int, fps: int) -> bytes:
    # Slowly cycling background hue makes motion obvious between frames.
    phase = (frame_num % 90) / 90.0
    r = int(20 + 35 * (0.5 + 0.5 * math.sin(phase * 2 * math.pi)))
    g = int(40 + 60 * phase)
    b = int(90 + 80 * (1.0 - phase))
    img = Image.new("RGB", (width, height), (r, g, b))
    draw = ImageDraw.Draw(img)

    big = pick_font(96)
    small = pick_font(28)

    label = str(frame_num)
    bbox = draw.textbbox((0, 0), label, font=big)
    draw.text(
        ((width - (bbox[2] - bbox[0])) / 2, height * 0.28),
        label,
        fill=(255, 230, 80),
        font=big,
    )

    draw.rectangle([0, height - 64, width, height], fill=(0, 0, 0))
    t = frame_num / fps
    draw.text(
        (16, height - 54),
        f"SRC frame {frame_num:04d}  t={t:06.3f}s  fps={fps}",
        fill=(255, 255, 255),
        font=small,
    )

    return img.tobytes()


def write_tone_wav(path: Path, duration: float, sample_rate: int) -> None:
    count = int(round(duration * sample_rate))
    t = np.arange(count) / sample_rate
    # 440 Hz carrier with a slow amplitude pulse so segments are locatable.
    pulse = 0.55 + 0.45 * (np.sin(2 * np.pi * 2 * t) > 0)
    samples = (0.5 * pulse * np.sin(2 * np.pi * 440 * t))
    pcm = (samples * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("output", type=Path)
    ap.add_argument("--duration", type=float, default=10.0)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=360)
    ap.add_argument("--sample-rate", type=int, default=48000)
    ap.add_argument("--ffmpeg", default="ffmpeg")
    args = ap.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    total_frames = round(args.duration * args.fps)
    wav_path = args.output.with_suffix(".wav")
    write_tone_wav(wav_path, args.duration, args.sample_rate)

    cmd = [
        args.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{args.width}x{args.height}", "-r", str(args.fps),
        "-i", "pipe:0",
        "-i", str(wav_path),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", str(args.fps),
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(args.output),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    for frame_num in range(total_frames):
        proc.stdin.write(render_frame(frame_num, args.width, args.height, args.fps))
    proc.stdin.close()
    rc = proc.wait()
    if rc:
        raise SystemExit(f"ffmpeg failed with rc={rc}")
    wav_path.unlink(missing_ok=True)
    print(f"wrote {args.output} ({total_frames} frames, {args.duration}s @ {args.fps}fps)")


if __name__ == "__main__":
    main()
