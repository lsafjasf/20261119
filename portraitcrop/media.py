"""ffmpeg-backed video probing, synthetic encoding, and crop export."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .models import CropSpec, Window


def find_ffmpeg(explicit: str | Path | None = None) -> str:
    candidates = [
        str(explicit) if explicit else None,
        os.environ.get("FFMPEG"),
        shutil.which("ffmpeg"),
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        str(Path.home() / "bin" / "ffmpeg"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise RuntimeError(
        "ffmpeg was not found; pass --ffmpeg /path/to/ffmpeg "
        "or set the FFMPEG environment variable"
    )


def find_ffprobe(
    explicit: str | Path | None = None,
    ffmpeg: str | Path | None = None,
) -> str | None:
    candidates = [
        str(explicit) if explicit else None,
        os.environ.get("FFPROBE"),
        shutil.which("ffprobe"),
        "/usr/bin/ffprobe",
        "/usr/local/bin/ffprobe",
    ]
    if ffmpeg:
        candidates.insert(0, str(Path(ffmpeg).with_name("ffprobe")))
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _rate_to_float(value: str | None) -> float | None:
    if not value:
        return None
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        if float(denominator) == 0:
            return None
        return float(numerator) / float(denominator)
    return float(value)


def probe_video(path: str | Path, ffprobe: str) -> dict[str, Any]:
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate,nb_frames,duration",
        "-of",
        "json",
        str(path),
    ]
    process = subprocess.run(
        command, capture_output=True, text=True, check=False
    )
    if process.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {process.stderr.strip()}")
    streams = json.loads(process.stdout).get("streams", [])
    if not streams:
        raise RuntimeError(f"no video stream found in {path}")
    stream = streams[0]
    fps = _rate_to_float(stream.get("avg_frame_rate")) or _rate_to_float(
        stream.get("r_frame_rate")
    )
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": fps,
        "frame_count": int(stream["nb_frames"])
        if str(stream.get("nb_frames", "")).isdigit()
        else None,
        "duration_s": float(stream["duration"])
        if stream.get("duration") not in (None, "N/A")
        else None,
    }


def encode_raw_frames(
    output: str | Path,
    width: int,
    height: int,
    fps: float,
    frames: Iterable[bytes],
    ffmpeg: str,
    crf: int = 23,
) -> int:
    frame_size = width * height * 3
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]
    with tempfile.TemporaryFile() as error_log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=error_log,
        )
        count = 0
        try:
            assert process.stdin is not None
            for frame in frames:
                if len(frame) != frame_size:
                    raise ValueError(
                        f"raw frame has {len(frame)} bytes, expected {frame_size}"
                    )
                process.stdin.write(frame)
                count += 1
            process.stdin.close()
        except BrokenPipeError as error:
            process.wait()
            error_log.seek(0)
            raise RuntimeError(
                "ffmpeg encoder stopped early: "
                + error_log.read().decode("utf-8", errors="replace")
            ) from error
        return_code = process.wait()
        if return_code != 0:
            error_log.seek(0)
            raise RuntimeError(
                "ffmpeg encoding failed: "
                + error_log.read().decode("utf-8", errors="replace")
            )
    return count


def _read_exact(stream: Any, size: int) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            return None if not chunks else bytes(chunks)
        chunks.extend(chunk)
    return bytes(chunks)


def export_crop_video(
    source_video: str | Path,
    output_video: str | Path,
    source_width: int,
    source_height: int,
    crop: CropSpec,
    windows: list[Window],
    fps: float,
    ffmpeg: str,
    keep_audio: bool = True,
    crf: int = 20,
) -> dict[str, Any]:
    if not windows:
        raise ValueError("cannot export a video without crop windows")
    Path(output_video).parent.mkdir(parents=True, exist_ok=True)
    for window in windows:
        if window.x < 0 or window.x + crop.width > source_width:
            raise ValueError(f"crop window is out of bounds at frame {window.frame}")
        if window.y < 0 or window.y + crop.height > source_height:
            raise ValueError(f"crop window is out of bounds at frame {window.frame}")

    frame_size = source_width * source_height * 3
    cropped_row_bytes = crop.width * 3
    decode_command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(source_video),
        "-frames:v",
        str(len(windows)),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-vsync",
        "0",
        "-",
    ]
    encode_command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{crop.width}x{crop.height}",
        "-r",
        str(fps),
        "-i",
        "-",
    ]
    if keep_audio:
        encode_command.extend(
            [
                "-i",
                str(source_video),
                "-map",
                "0:v:0",
                "-map",
                "1:a?",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-shortest",
            ]
        )
    encode_command.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_video),
        ]
    )

    with tempfile.TemporaryFile() as decoder_log, tempfile.TemporaryFile() as encoder_log:
        decoder = subprocess.Popen(
            decode_command,
            stdout=subprocess.PIPE,
            stderr=decoder_log,
        )
        encoder = subprocess.Popen(
            encode_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=encoder_log,
        )
        exported = 0
        try:
            assert decoder.stdout is not None
            assert encoder.stdin is not None
            for window in windows:
                raw_frame = _read_exact(decoder.stdout, frame_size)
                if raw_frame is None or len(raw_frame) != frame_size:
                    raise RuntimeError(
                        f"decoder ended at frame {window.frame}; "
                        f"expected {len(windows)} frames"
                    )
                cropped = bytearray(crop.width * crop.height * 3)
                for row in range(crop.height):
                    source_start = (
                        (crop.y + row) * source_width + window.x
                    ) * 3
                    target_start = row * cropped_row_bytes
                    cropped[target_start : target_start + cropped_row_bytes] = (
                        raw_frame[source_start : source_start + cropped_row_bytes]
                    )
                encoder.stdin.write(cropped)
                exported += 1
            encoder.stdin.close()
        except BrokenPipeError as error:
            decoder.kill()
            encoder.wait()
            encoder_log.seek(0)
            raise RuntimeError(
                "crop encoder stopped early: "
                + encoder_log.read().decode("utf-8", errors="replace")
            ) from error
        except Exception:
            decoder.kill()
            encoder.kill()
            decoder.wait()
            encoder.wait()
            raise
        finally:
            if decoder.stdout is not None:
                decoder.stdout.close()
            if encoder.stdin is not None and not encoder.stdin.closed:
                try:
                    encoder.stdin.close()
                except BrokenPipeError:
                    pass
            if encoder.poll() is None and exported != len(windows):
                encoder.kill()

        decoder_code = decoder.wait()
        encoder_code = encoder.wait()
        if decoder_code != 0:
            decoder_log.seek(0)
            raise RuntimeError(
                "crop decoder failed: "
                + decoder_log.read().decode("utf-8", errors="replace")
            )
        if encoder_code != 0:
            encoder_log.seek(0)
            raise RuntimeError(
                "crop encoder failed: "
                + encoder_log.read().decode("utf-8", errors="replace")
            )
    return {
        "path": str(output_video),
        "width": crop.width,
        "height": crop.height,
        "fps": fps,
        "frame_count": exported,
        "audio_preserved": keep_audio,
    }
