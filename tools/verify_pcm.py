#!/usr/bin/env python3
"""Decode two Ogg Vorbis files with libsndfile (independent of the
repaginator) and verify their PCM output is identical."""

import sys

import numpy as np
import soundfile as sf


def decode(path):
    data, samplerate = sf.read(path, dtype="float64", always_2d=True)
    return samplerate, data


def main(argv):
    if len(argv) != 3:
        print(f"usage: {argv[0]} FILE_A FILE_B")
        return 2
    sr_a, pcm_a = decode(argv[1])
    sr_b, pcm_b = decode(argv[2])
    if sr_a != sr_b:
        print(f"FAIL: samplerate differs {sr_a} vs {sr_b}")
        return 1
    if pcm_a.shape != pcm_b.shape:
        print(f"FAIL: shape differs {pcm_a.shape} vs {pcm_b.shape}")
        return 1
    diff = np.abs(pcm_a - pcm_b)
    max_diff = float(diff.max()) if diff.size else 0.0
    frames, channels = pcm_a.shape
    print(f"samplerate={sr_a} Hz  channels={channels}  frames={frames}")
    print(f"max |PCM difference| = {max_diff:.3e}")
    if max_diff == 0.0:
        print("PASS: PCM is bit-identical")
        return 0
    print("FAIL: PCM differs")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
