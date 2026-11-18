#!/usr/bin/env python3
"""Insert a frozen-frame (hold) clip into a video timeline.

The visual content stays constant during the freeze while every output frame
keeps a correct, monotonically increasing presentation timestamp. Frames after
the insertion point shift later by exactly the freeze duration.

Audio strategies
----------------
continue : original audio keeps playing across the freeze (no duration added to
           the audio track; it ends when the source audio ends).
mute     : silence replaces the audio while the frozen picture is shown; the
           original audio resumes afterwards (audio track grows by the freeze).
extend   : the source sample at the freeze point is held for the freeze
           duration, then the original audio resumes (audio track grows).
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


AUDIO_STRATEGIES = ("continue", "mute", "extend")


@dataclass
class InsertSpec:
    source: str
    output: str
    freeze_at: float          # seconds on the source timeline
    freeze_duration: float    # seconds of frozen picture
    audio: str = "mute"
    fps: int | None = None    # override; otherwise probed from source


def run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed (rc={proc.returncode}): {' '.join(shlex.quote(c) for c in cmd)}\n"
            f"{proc.stderr.strip()}"
        )
    return proc.stdout


def probe(path: Path, ffprobe: str) -> dict:
    out = run([
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    return json.loads(out)


def video_stream(info: dict) -> dict:
    for stream in info["streams"]:
        if stream["codec_type"] == "video":
            return stream
    raise RuntimeError("no video stream found")


def audio_stream(info: dict) -> dict | None:
    for stream in info["streams"]:
        if stream["codec_type"] == "audio":
            return stream
    return None


def rational_fps(stream: dict) -> tuple[int, int]:
    num, den = stream["r_frame_rate"].split("/")
    return int(num), int(den)


def build_filter(
    spec: InsertSpec,
    fps_num: int,
    fps_den: int,
    has_audio: bool,
    sample_rate: int = 48000,
    channels: int = 1,
) -> str:
    fps = fps_num / fps_den
    # Snap the freeze point to an actual source frame boundary.
    freeze_frame = round(spec.freeze_at * fps)
    freeze_at = freeze_frame / fps
    hold_frames = max(1, round(spec.freeze_duration * fps))
    hold_duration = hold_frames / fps

    parts: list[str] = []
    # Split the picture into the portion before the freeze and the portion
    # starting at the frozen source frame.
    parts.append(
        f"[0:v]split=2[vsrc_pre][vsrc_rest];"
        f"[vsrc_pre]trim=0:{freeze_at:.9f},setpts=PTS-STARTPTS[vpre];"
        f"[vsrc_rest]trim=start_frame={freeze_frame},setpts=PTS-STARTPTS[vrest];"
        f"[vrest]split=2[vfreeze_src][vpost];"
        # Freeze: keep only the first frame of the rest, loop it for the hold.
        f"[vfreeze_src]trim=end_frame=1,setpts=PTS-STARTPTS,"
        f"loop=loop={hold_frames - 1}:size=1:start=0,"
        # Rebase timestamps with the stream time base so every held frame gets
        # its own monotonically increasing PTS (a plain fps filter would drop
        # the repeated frames as duplicates sharing one timestamp).
        f"setpts=N/(({fps_num}/{fps_den})*TB)[vfreeze]"
    )
    parts.append(
        f"[vpre][vfreeze][vpost]concat=n=3:v=1:a=0[vout]"
    )

    if has_audio:
        if spec.audio == "continue":
            # Audio is untouched: it continues playing across the frozen picture.
            parts.append("[0:a]anull[aout]")
        else:
            parts.append(
                "[0:a]asplit=2[apre_src][apost_src];"
                f"[apre_src]atrim=0:{freeze_at:.9f},asetpts=PTS-STARTPTS[apre];"
                f"[apost_src]atrim=start={freeze_at:.9f},asetpts=PTS-STARTPTS[apost]"
            )
            if spec.audio == "mute":
                layout = "mono" if channels == 1 else "stereo"
                parts.append(
                    f"anullsrc=r={sample_rate}:cl={layout}:d={hold_duration:.9f}[asilence];"
                    f"[apre][asilence][apost]concat=n=3:v=0:a=1[aout]"
                )
            elif spec.audio == "extend":
                # Capture one source frame's worth of audio and loop it for the
                # whole freeze so the held picture keeps a held sound.
                parts.append(
                    f"[0:a]atrim=start={freeze_at:.9f}:end={freeze_at + 1 / fps:.9f},"
                    f"asetpts=PTS-STARTPTS,aloop=loop=-1:size={sample_rate // fps_num}:start=0,"
                    f"atrim=0:{hold_duration:.9f},asetpts=PTS-STARTPTS[ahold];"
                    f"[apre][ahold][apost]concat=n=3:v=0:a=1[aout]"
                )
            else:  # pragma: no cover - guarded by argparse choices
                raise ValueError(f"unknown audio strategy: {spec.audio}")

    return ";".join(parts), freeze_at, hold_duration


def insert_freeze(spec: InsertSpec, ffmpeg: str, ffprobe: str) -> dict:
    source = Path(spec.source)
    output = Path(spec.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if spec.audio not in AUDIO_STRATEGIES:
        raise ValueError(f"audio must be one of {AUDIO_STRATEGIES}")

    info = probe(source, ffprobe)
    vstream = video_stream(info)
    astream = audio_stream(info)
    fps_num, fps_den = rational_fps(vstream)
    if spec.fps is not None:
        fps_num, fps_den = spec.fps, 1
    sample_rate = int(astream["sample_rate"]) if astream else 48000
    channels = int(astream.get("channels", 1)) if astream else 1

    filtergraph, freeze_at, hold_duration = build_filter(
        spec, fps_num, fps_den,
        has_audio=astream is not None,
        sample_rate=sample_rate, channels=channels,
    )

    map_args = ["-map", "[vout]"]
    if astream is not None:
        map_args += ["-map", "[aout]", "-c:a", "aac", "-b:a", "128k"]
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-filter_complex", filtergraph,
        *map_args,
        # Pin the output to the source frame rate in CFR mode; otherwise the
        # default 25 fps muxer rate would silently drop the held frames.
        "-r", f"{fps_num}/{fps_den}", "-fps_mode", "cfr",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output),
    ]
    run(cmd)

    result = {
        **asdict(spec),
        "output": str(output),
        "snapped_freeze_at": freeze_at,
        "actual_hold_duration": hold_duration,
        "fps": fps_num / fps_den,
        "has_audio": astream is not None,
        "filtergraph": filtergraph,
    }
    sidecar = output.with_suffix(output.suffix + ".json")
    sidecar.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--at", dest="freeze_at", type=float, required=True,
                    help="freeze point in seconds on the source timeline")
    ap.add_argument("--duration", dest="freeze_duration", type=float, required=True,
                    help="duration of the frozen clip in seconds")
    ap.add_argument("--audio", choices=AUDIO_STRATEGIES, default="mute")
    ap.add_argument("--fps", type=int, default=None)
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--ffprobe", default="ffprobe")
    args = ap.parse_args()

    spec = InsertSpec(
        source=str(args.source),
        output=str(args.output),
        freeze_at=args.freeze_at,
        freeze_duration=args.freeze_duration,
        audio=args.audio,
        fps=args.fps,
    )
    result = insert_freeze(spec, args.ffmpeg, args.ffprobe)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
