from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from portraitcrop.media import encode_raw_frames, export_crop_video, find_ffprobe, probe_video
from portraitcrop.models import CropSpec, Window


FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg")


@unittest.skipUnless(FFMPEG, "ffmpeg is not available")
class MediaIntegrationTests(unittest.TestCase):
    def test_encode_and_crop_video(self) -> None:
        width, height, fps, frame_count = 320, 180, 10, 5
        crop = CropSpec(width, height, 102, height, 0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "crop.mp4"
            frames = []
            for index in range(frame_count):
                color = bytes((20 + index * 10, 80, 140))
                frames.append(color * (width * height))
            count = encode_raw_frames(
                source, width, height, fps, frames, str(FFMPEG), crf=28
            )
            windows = [
                Window(index, index / fps, index * 4, 0, crop.width, crop.height, 1.0)
                for index in range(frame_count)
            ]
            export_crop_video(
                source,
                output,
                width,
                height,
                crop,
                windows,
                fps,
                str(FFMPEG),
                keep_audio=False,
                crf=28,
            )
            self.assertEqual(count, frame_count)
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 0)
            ffprobe = find_ffprobe(ffmpeg=FFMPEG)
            if ffprobe:
                metadata = probe_video(output, ffprobe)
                self.assertEqual(metadata["width"], crop.width)
                self.assertEqual(metadata["height"], crop.height)
                self.assertEqual(metadata["frame_count"], frame_count)


if __name__ == "__main__":
    unittest.main()
