#!/usr/bin/env python3
"""Command-line entry point for the feedback-suppressor experiments.

Examples
--------
python run_experiment.py                      # all scenarios, table output
python run_experiment.py --scenario drifting_howl --json out.json
python run_experiment.py --plots artifacts    # also render PNG plots
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from afs.report import metrics_table, write_json
from afs.scenarios import SCENARIOS, run_scenario


def _false_alarms(name: str, controlled) -> int:
    if name == "drifting_howl":
        return 0
    return controlled.controller.bank.active_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=sorted(SCENARIOS),
        action="append",
        help="Scenario to run (repeatable); default runs every scenario.",
    )
    parser.add_argument("--json", help="Optional path for JSON metrics.")
    parser.add_argument(
        "--plots",
        help="Optional directory for PNG diagnostic plots (headless safe).",
    )
    args = parser.parse_args()

    names = args.scenario or sorted(SCENARIOS)
    records = []
    plot_payloads = {}
    for name in names:
        scenario, controlled, reference, metrics = run_scenario(name)
        record = {
            "name": name,
            "description": scenario.description,
            "metrics": metrics,
            "false_alarms": _false_alarms(name, controlled),
            "config": {
                "fs": scenario.plant.fs,
                "delay_ms": scenario.plant.delay_ms,
                "loop_gain": scenario.plant.loop_gain,
                "resonance_q": scenario.plant.resonance_q,
                "max_notches": scenario.controller.max_notches,
                "notch_q": scenario.controller.q,
            },
        }
        records.append(record)
        plot_payloads[name] = (controlled, reference)

    print(metrics_table(records))

    if args.json:
        write_json(records, args.json)
        print(f"\nJSON metrics written to {args.json}")

    if args.plots:
        from afs.dashboard import render_scenario_png

        out_dir = Path(args.plots)
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, (controlled, reference) in plot_payloads.items():
            png = out_dir / f"{name}.png"
            render_scenario_png(name, controlled, reference, str(png))
            print(f"plot written to {png}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
