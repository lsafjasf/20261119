from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from audio_merge.web import build_handler
from audio_merge.normalize import load_project


ROOT = Path(__file__).resolve().parents[1]


def post(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        base = load_project(ROOT / "examples/base.json")
        left = load_project(ROOT / "examples/conflict_left.json")
        right = load_project(ROOT / "examples/conflict_right.json")
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        handler = build_handler(base, left, right, Path(self.tmp.name))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.shutdown)

    @property
    def root_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def test_full_conflict_resolution_flow(self):
        state = get(f"{self.root_url}/api/state")
        self.assertEqual(state["unresolvedCount"], 3)
        ids = [c["id"] for c in state["conflicts"]]
        self.assertEqual(ids, ["C1", "C2", "C3"])

        # Export must be refused while conflicts are open.
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            post(f"{self.root_url}/api/export", {})
        self.assertEqual(ctx.exception.code, 400)

        post(f"{self.root_url}/api/resolve",
             {"conflictId": "C1", "choice": "right"})
        post(f"{self.root_url}/api/resolve",
             {"conflictId": "C2", "choice": "base"})
        state = post(f"{self.root_url}/api/resolve",
                     {"conflictId": "C3", "choice": "delete"})
        self.assertEqual(state["unresolvedCount"], 0)

        exported = post(f"{self.root_url}/api/export", {})
        self.assertTrue(exported["ok"])
        project_path = Path(exported["projectPath"])
        self.assertTrue(project_path.exists())
        self.assertTrue(Path(exported["logPath"]).exists())
        project = json.loads(project_path.read_text(encoding="utf-8"))
        text = json.dumps(project)
        self.assertNotIn("__unresolved__", text)
        self.assertNotIn("__conflict__", text)

    def test_reset_reopens_conflicts(self):
        post(f"{self.root_url}/api/resolve",
             {"conflictId": "C2", "choice": "left"})
        state = get(f"{self.root_url}/api/state")
        self.assertEqual(state["unresolvedCount"], 2)
        state = post(f"{self.root_url}/api/reset", {"conflictId": "C2"})
        self.assertEqual(state["unresolvedCount"], 3)


if __name__ == "__main__":
    unittest.main()
