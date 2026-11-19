"""Command-line entry: merge an exposure bracket into linear irradiance."""

import argparse
import json
import os

import numpy as np

from .merge import merge_exposures
from .synthetic import generate_test_sequence


def _load_user_sequence(input_dir):
    """Load frames named 0.npy..N.npy plus an exposures.json list."""
    with open(os.path.join(input_dir, "exposures.json")) as f:
        exposures = np.asarray(json.load(f), dtype=np.float64)
    frames = []
    index = 0
    while True:
        path = os.path.join(input_dir, f"{index}.npy")
        if not os.path.exists(path):
            break
        frames.append(np.load(path))
        index += 1
    if not frames:
        raise FileNotFoundError(f"no frame .npy files found in {input_dir}")
    images = np.stack(frames).astype(np.uint8)
    if images.ndim == 3:
        images = np.repeat(images[..., None], 3, axis=-1)
    if len(images) != len(exposures):
        raise ValueError("frame count does not match exposure metadata")
    return images, exposures


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Merge an exposure bracket into float linear irradiance."
    )
    parser.add_argument(
        "--input",
        help="directory with 0.npy..N.npy frames and exposures.json; "
        "if omitted the built-in synthetic sequence is used",
    )
    parser.add_argument(
        "--out", default="artifacts", help="output directory"
    )
    args = parser.parse_args(argv)

    if args.input:
        images, exposures = _load_user_sequence(args.input)
    else:
        sequence = generate_test_sequence()
        images, exposures = sequence.images, sequence.exposures

    result = merge_exposures(images, exposures)
    os.makedirs(args.out, exist_ok=True)
    np.save(os.path.join(args.out, "irradiance.npy"), result.irradiance)
    np.save(os.path.join(args.out, "valid_mask.npy"), result.valid)
    np.save(os.path.join(args.out, "motion_mask.npy"), result.motion_mask)
    summary = {
        "frames_in": int(len(images)),
        "rejected_frames": result.rejected.tolist(),
        "valid_fraction": float(result.valid.mean()),
        "motion_fraction": float(result.motion_mask.mean()),
    }
    with open(os.path.join(args.out, "merge_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
