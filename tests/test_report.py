from __future__ import annotations

import json

from varispeed.report import build_suite


def test_demo_suite_writes_audio_plots_and_metrics(tmp_path):
    root = tmp_path / "artifacts"
    report = build_suite(root, sample_rate=16_000, speeds=(0.5, 1.0, 2.0))
    assert (root / "index.html").is_file()
    assert (root / "report.json").is_file()
    parsed = json.loads((root / "report.json").read_text(encoding="utf-8"))
    assert parsed["metadata"]["sample_rate"] == 16_000
    for name in ("tone", "sweep", "pulse"):
        entry = report["tests"][name]
        assert (root / entry["source_audio"]).is_file()
        for label, result in entry["speeds"].items():
            assert result["output_samples"] == result["expected_samples"]
            assert result["ola_no_holes"]
            assert (root / result["audio"]).is_file()
            assert (root / result["plot"]).is_file()
            assert (root / result["data"]).is_file()
    assert report["realtime"]["realtime_no_gap"]
    assert report["realtime"]["offline_no_gap"]
    for item in report["listening_demo"]["speeds"].values():
        assert (root / item["audio"]).is_file()
