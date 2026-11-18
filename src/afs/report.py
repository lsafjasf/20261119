"""Tabular and JSON reporting for scenario runs."""

from __future__ import annotations

import json

import numpy as np


def _fmt(value, spec: str = "") -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return format(value, spec)
    return str(value)


def metrics_table(records: list[dict]) -> str:
    headers = [
        "scenario",
        "detect_s",
        "notch_s",
        "settle_s",
        "latency_s",
        "peak_dB",
        "off_target_dB",
        "notches",
        "false_alarms",
    ]
    rows = [headers]
    for record in records:
        metrics = record["metrics"]
        rows.append(
            [
                record["name"],
                _fmt(metrics["first_detection_s"], ".3f"),
                _fmt(metrics["first_notch_s"], ".3f"),
                _fmt(metrics["settling_time_s"], ".3f"),
                _fmt(metrics["suppression_latency_s"], ".3f"),
                _fmt(metrics["remaining_peak_db"], ".2f"),
                _fmt(metrics["off_target_loss_db"], ".3f"),
                str(metrics["notches_used"]),
                str(record.get("false_alarms", 0)),
            ]
        )
    widths = [max(len(row[col]) for row in rows) for col in range(len(headers))]
    lines = []
    for row_index, row in enumerate(rows):
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
        if row_index == 0:
            lines.append("  ".join("-" * width for width in widths))
    return "\n".join(lines)


def to_jsonable(value):
    if isinstance(value, dict):
        return {key: to_jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    return value


def write_json(records: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(to_jsonable(records), handle, indent=2, ensure_ascii=False)
