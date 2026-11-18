"""Command-line interface for the LPC laboratory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .benchmark import build_demo, run_stability_audit, run_sweep
from .codec import decode_streams, encode_file
from .dsp import AnalysisConfig, analyze_signal
from .report import render_report
from .summary import render_summary
from .wavio import read_wav, write_wav


def _integer_list(value: str) -> list[int]:
    try:
        values = [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from error
    if not values:
        raise argparse.ArgumentTypeError("list must not be empty")
    return values


def _config(args: argparse.Namespace, sample_rate: int) -> AnalysisConfig:
    return AnalysisConfig(
        sample_rate=sample_rate,
        frame_length=args.frame_length,
        hop_length=args.frame_length,
        window_length=args.window_length,
        order=args.order,
        window_type=args.window_type,
        preemphasis=args.preemphasis,
    )


def command_analyze(args: argparse.Namespace) -> None:
    samples, sample_rate = read_wav(args.input)
    config = _config(args, sample_rate)
    analysis = analyze_signal(samples, config)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(analysis, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    stable = sum(1 for frame in analysis["frames"] if frame["stable"])
    print(
        f"analyzed {analysis['frame_count']} frames: "
        f"{stable} stable, order={config.order}, window={config.window_length}"
    )
    print(f"analysis JSON: {output}")


def command_encode(args: argparse.Namespace) -> None:
    samples, sample_rate = read_wav(args.input)
    config = _config(args, sample_rate)
    encoded = encode_file(
        args.input,
        args.prefix,
        config,
        coefficient_mode=args.coefficient_mode,
        coefficient_bits=args.coefficient_bits,
        residual_bits=args.residual_bits,
    )
    print(f"coefficient stream: {encoded.coef_path}")
    print(f"residual stream:    {encoded.residual_path}")
    print(f"reconstruction:     {Path(args.prefix).with_suffix('.recon.wav')}")
    print(
        f"reconstruction SNR: {encoded.reconstruction_snr_db:.2f} dB; "
        f"rate: {encoded.sizes['total_kilobits_per_second']:.1f} kbit/s"
    )
    print(
        f"stability: max pole={encoded.stability['max_quantized_pole_radius']:.6f}, "
        f"stabilized={encoded.stability['stabilized']}, "
        f"rejected={encoded.stability['rejected']}"
    )


def command_decode(args: argparse.Namespace) -> None:
    samples, metadata = decode_streams(args.coefficients, args.residual)
    write_wav(args.output, samples, int(metadata["coef_header"]["sample_rate"]))
    print(
        f"decoded {len(samples)} samples to {args.output}; "
        f"order={metadata['order']}, mode={metadata['coefficient_mode']}"
    )


def command_benchmark(args: argparse.Namespace) -> None:
    inputs = {}
    for path_text in args.inputs:
        path = Path(path_text)
        stem = path.stem
        key = stem
        index = 2
        while key in inputs:
            key = f"{stem}_{index}"
            index += 1
        inputs[key] = path
    output = Path(args.output)
    sweep = run_sweep(
        inputs,
        args.orders,
        args.windows,
        output,
        frame_length=args.frame_length,
        coefficient_bits=args.coefficient_bits,
        residual_bits=args.residual_bits,
    )
    sweep["probes"] = {
        name: {"label": name, "path": str(path)}
        for name, path in inputs.items()
    }
    sweep["stability_audit"] = run_stability_audit(
        inputs,
        frame_length=args.frame_length,
        window_length=max(args.windows),
    )
    json_path = output / "benchmark.json"
    json_path.write_text(
        json.dumps(sweep, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    report_path = render_report(sweep, output / "lpc_report.html")
    summary_path = render_summary(sweep, output / "RESULTS.md")
    print(f"benchmark JSON: {json_path}")
    print(f"interactive report: {report_path}")
    print(f"summary: {summary_path}")
    print(f"streams: {output / 'streams'}")


def command_demo(args: argparse.Namespace) -> None:
    data = build_demo(
        args.samples,
        args.artifacts,
        orders=args.orders,
        windows=args.windows,
        frame_length=args.frame_length,
    )
    artifacts = Path(args.artifacts)
    json_path = artifacts / "benchmark.json"
    json_path.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    report_path = render_report(data, artifacts / "lpc_report.html")
    summary_path = render_summary(data, artifacts / "RESULTS.md")
    print(f"samples: {Path(args.samples).resolve()}")
    print(f"benchmark JSON: {json_path.resolve()}")
    print(f"interactive report: {report_path.resolve()}")
    print(f"summary: {summary_path.resolve()}")
    print(f"streams: {(artifacts / 'streams').resolve()}")
    print(
        "decode example: python3 decode_stream.py "
        f"{artifacts / 'streams' / 'vowel_o12'}.coef "
        f"{artifacts / 'streams' / 'vowel_o12'}.res /tmp/vowel.wav"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lpc-lab",
        description="Dependency-free LPC speech coding laboratory.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="write per-frame analysis JSON")
    analyze.add_argument("input", help="mono/stereo PCM WAV")
    analyze.add_argument("--output", default="analysis.json")
    analyze.add_argument("--order", type=int, default=12)
    analyze.add_argument("--frame-length", type=int, default=400)
    analyze.add_argument("--window-length", type=int, default=320)
    analyze.add_argument("--window-type", choices=("hann", "rectangular"), default="hann")
    analyze.add_argument("--preemphasis", type=float, default=0.0)
    analyze.set_defaults(handler=command_analyze)

    encode = subparsers.add_parser("encode", help="write .coef/.res/reconstruction")
    encode.add_argument("input", help="mono/stereo PCM WAV")
    encode.add_argument("--prefix", required=True, help="output path without suffix")
    encode.add_argument("--order", type=int, default=12)
    encode.add_argument("--frame-length", type=int, default=400)
    encode.add_argument("--window-length", type=int, default=320)
    encode.add_argument("--window-type", choices=("hann", "rectangular"), default="hann")
    encode.add_argument("--preemphasis", type=float, default=0.0)
    encode.add_argument("--coefficient-mode", choices=("rc", "direct"), default="rc")
    encode.add_argument("--coefficient-bits", type=int, default=8)
    encode.add_argument("--residual-bits", type=int, default=8)
    encode.set_defaults(handler=command_encode)

    decode = subparsers.add_parser("decode", help="decode coefficient/residual streams")
    decode.add_argument("coefficients")
    decode.add_argument("residual")
    decode.add_argument("output")
    decode.set_defaults(handler=command_decode)

    benchmark = subparsers.add_parser("benchmark", help="sweep orders/windows and build report")
    benchmark.add_argument("inputs", nargs="+", help="one or more PCM WAV files")
    benchmark.add_argument("--orders", type=_integer_list, default=[4, 6, 8, 10, 12, 16])
    benchmark.add_argument("--windows", type=_integer_list, default=[160, 240, 320])
    benchmark.add_argument("--frame-length", type=int, default=400)
    benchmark.add_argument("--coefficient-bits", type=int, default=8)
    benchmark.add_argument("--residual-bits", type=int, default=8)
    benchmark.add_argument("--output", default="artifacts")
    benchmark.set_defaults(handler=command_benchmark)

    demo = subparsers.add_parser("demo", help="generate probes, streams and HTML report")
    demo.add_argument("--samples", default="samples")
    demo.add_argument("--artifacts", default="artifacts")
    demo.add_argument("--orders", type=_integer_list, default=[4, 6, 8, 10, 12, 16])
    demo.add_argument("--windows", type=_integer_list, default=[160, 240, 320])
    demo.add_argument("--frame-length", type=int, default=400)
    demo.set_defaults(handler=command_demo)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.handler(args)

