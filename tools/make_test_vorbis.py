"""Generate a legal Ogg Vorbis test file inside the project."""
import numpy as np
import soundfile as sf

SR = 44100
SECONDS = 6
t = np.arange(int(SR * SECONDS)) / SR
# chirp 220Hz -> 4kHz + harmonics + a little noise, stereo de-correlated
phase = 2 * np.pi * (220 * t + (4000 - 220) / (2 * SECONDS) * t ** 2)
left = 0.6 * np.sin(phase) + 0.2 * np.sin(3 * phase)
right = 0.6 * np.sin(phase * 1.003 + 0.5) + 0.15 * np.sin(2.5 * phase)
rng = np.random.default_rng(42)
noise = 0.02 * rng.standard_normal(len(t))
data = np.stack([left + noise, right - noise], axis=1).astype(np.float64)

path = "/home/hj/userfeedback/uid4-0005/out/test_input.ogg"
sf.write(path, data, SR, format="OGG", subtype="VORBIS")
info = sf.info(path)
print(info)
print("bytes:", __import__("os").path.getsize(path))
