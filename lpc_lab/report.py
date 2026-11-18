"""Generate a self-contained interactive HTML laboratory report."""

from __future__ import annotations

import json
import time
from pathlib import Path

TEMPLATE_DIR = Path(__file__).with_name("templates")


def render_report(data: dict, output_path: str | Path) -> Path:
    template = (TEMPLATE_DIR / "report.html").read_text(encoding="utf-8")
    stylesheet = (TEMPLATE_DIR / "report.css").read_text(encoding="utf-8")
    javascript = (TEMPLATE_DIR / "report.js").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e")
    generated = data.get(
        "generated_utc",
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    document = (
        template.replace("{{CSS}}", stylesheet)
        .replace("{{JS}}", javascript)
        .replace("{{DATA}}", payload)
        .replace("{{GENERATED}}", generated)
    )
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")
    return destination

