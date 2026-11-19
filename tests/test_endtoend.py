"""End-to-end smoke test: synthetic generation, calibration, degeneracy."""

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(*argv):
    return subprocess.run([sys.executable, "-m", "calibtool", *argv],
                          cwd=ROOT, capture_output=True, text=True)


def test_recovers_ground_truth():
    with tempfile.TemporaryDirectory() as tmp:
        data = os.path.join(tmp, "ok")
        assert run("generate", data, "--count", "14").returncode == 0
        out = os.path.join(tmp, "params.json")
        proc = run("calibrate", data, "--out", out)
        assert proc.returncode == 0, proc.stderr
        params = json.load(open(out))
        truth = json.load(open(os.path.join(data, "ground_truth.json")))
        cam = truth["camera"]
        fx = params["kmat"][0][0]
        k1, k2 = params["dist"][0], params["dist"][1]
        assert abs(fx - cam["fx"]) / cam["fx"] < 0.015
        assert abs(k1 - cam["k1"]) < 0.02
        assert abs(k2 - cam["k2"]) < 0.02
        assert not params["degenerate"]


def test_degenerate_poses_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        data = os.path.join(tmp, "deg")
        assert run("generate", data, "--degenerate").returncode == 0
        out = os.path.join(tmp, "params.json")
        proc = run("calibrate", data, "--out", out)
        assert proc.returncode == 3
        assert "UNRELIABLE CALIBRATION" in proc.stdout
        assert not os.path.exists(out)


if __name__ == "__main__":
    test_recovers_ground_truth()
    test_degenerate_poses_rejected()
    print("all end-to-end tests passed")
