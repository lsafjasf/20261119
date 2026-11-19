"""Command-line entry point for the blind deconvolution experiment."""

from __future__ import annotations

import argparse
from pathlib import Path

from .experiment import ExperimentConfig, run_experiment
from .solver import SolverConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run truth-blind 2-D motion deconvolution on synthetic samples."
    )
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument("--image-size", type=int, default=192)
    parser.add_argument("--max-kernel-size", type=int, default=21)
    parser.add_argument("--iterations", type=int, default=24)
    parser.add_argument("--ensemble-members", type=int, default=5)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--dpi", type=int, default=150)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.max_kernel_size % 2 == 0:
        raise SystemExit("--max-kernel-size must be odd")
    if args.image_size < 64:
        raise SystemExit("--image-size must be at least 64")
    solver = SolverConfig(max_iterations=args.iterations, seed=args.seed)
    config = ExperimentConfig(
        output_dir=args.output_dir,
        image_size=args.image_size,
        max_kernel_size=args.max_kernel_size,
        ensemble_members=args.ensemble_members,
        solver=solver,
        dpi=args.dpi,
    )
    results = run_experiment(config)
    print(f"\nWrote {len(results)} cases to {args.output_dir.resolve()}")
    print(f"Open {(args.output_dir / 'report.html').resolve()}")


if __name__ == "__main__":
    main()
